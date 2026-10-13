"""Registration, sign-in and account administration."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from agentlab.accounts import Principal, UserView
from agentlab.api.routes.common import (
    RESPONSES,
    SESSION_COOKIE,
    client_of,
    current_principal,
    require_admin,
    require_signed_in,
)
from agentlab.api.schemas import (
    AccountEventOut,
    AccountOut,
    AuthOptions,
    ChangePasswordRequest,
    CreateUserRequest,
    LoginRequest,
    MeOut,
    RegisterRequest,
    RegistrationOut,
    SessionOut,
    TemporaryPassword,
    UpdateUserRequest,
)
from agentlab.api.state import ApiState, state_of
from agentlab.core.errors import UserError

public = APIRouter(responses=RESPONSES)
router = APIRouter(responses=RESPONSES)


def _service(st: ApiState) -> Any:
    if st.accounts is None:  # only a server built without a database could be here
        raise UserError("accounts are not available on this server")
    return st.accounts


def _account(user: UserView) -> AccountOut:
    return AccountOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=user.role,
        status=user.status,  # type: ignore[arg-type]
        must_change_password=user.must_change_password,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        locked_until=user.locked_until,
    )


def _me(principal: Principal) -> MeOut:
    return MeOut(
        kind=principal.kind,
        role=principal.role,
        username=principal.username,
        display_name=principal.display_name,
        must_change_password=principal.must_change_password,
        csrf_token=principal.csrf_token,
        operator=principal.is_operator and not principal.must_change_password,
        can_manage_accounts=principal.is_admin and not principal.must_change_password,
    )


def _secure(request: Request, st: ApiState) -> bool:
    mode = st.services.config.accounts.cookie_secure
    if mode != "auto":
        return mode == "always"
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def _set_cookie(response: Response, request: Request, st: ApiState, token: str, max_age: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        samesite="strict",
        secure=_secure(request, st),
        path="/",
    )


# ================================================================================================= public
@public.get(
    "/auth/options",
    response_model=AuthOptions,
    tags=["Accounts"],
    summary="How people can sign in here",
    description="Answers without a token: whether registration is open, how long a password must be, and which ways of "
    "signing in this server offers. The sign-in page asks this before anyone is signed in.",
)
def auth_options(request: Request) -> AuthOptions:
    st = state_of(request)
    cfg = st.services.config.accounts
    return AuthOptions(
        registration=cfg.registration,
        min_password_length=cfg.min_password_length,
        api_token_sign_in=st.guard.required,
        password_sign_in=st.accounts is not None,
    )


@public.post(
    "/auth/register",
    response_model=RegistrationOut,
    status_code=201,
    tags=["Accounts"],
    summary="Register an account",
    description="Creates an account with the server's default role (a reporter unless the owner chose otherwise) and, when "
    "registration is open, signs the person in. With `approval` the account waits for an administrator; with `closed` this "
    "is refused. Registrations are limited per client address.",
)
def register(body: RegisterRequest, request: Request, response: Response) -> RegistrationOut:
    st = state_of(request)
    svc = _service(st)
    user = svc.register(
        body.username, body.password, display_name=body.display_name, email=body.email, client=client_of(request)
    )
    if user.status != "active":
        return RegistrationOut(account=_account(user), signed_in=False, me=None)
    token, csrf, expires = svc.create_session(
        user.id, client=client_of(request), user_agent=request.headers.get("user-agent", "")
    )
    cfg = st.services.config.accounts
    _set_cookie(response, request, st, token, cfg.session_absolute_hours * 3600)
    principal = Principal(
        kind="user",
        role=user.role,
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        must_change_password=user.must_change_password,
        csrf_token=csrf,
    )
    return RegistrationOut(account=_account(user), signed_in=True, me=_me(principal))


@public.post(
    "/auth/login",
    response_model=SessionOut,
    tags=["Accounts"],
    summary="Sign in with a password",
    description="Checks the user name and password and starts a session: the cookie is HttpOnly and SameSite=Strict (Secure "
    "over HTTPS). The answer carries the CSRF token to send as `X-CSRF-Token` on requests that change something. Wrong "
    "answers are slowed down, and repeated wrong passwords lock the account for a while.",
)
def login(body: LoginRequest, request: Request, response: Response) -> SessionOut:
    st = state_of(request)
    svc = _service(st)
    user = svc.authenticate(body.username, body.password, client=client_of(request))
    token, csrf, expires = svc.create_session(
        user.id, client=client_of(request), user_agent=request.headers.get("user-agent", "")
    )
    cfg = st.services.config.accounts
    _set_cookie(response, request, st, token, cfg.session_absolute_hours * 3600)
    principal = Principal(
        kind="user",
        role=user.role,
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        must_change_password=user.must_change_password,
        csrf_token=csrf,
    )
    return SessionOut(me=_me(principal), account=_account(user))


# ==================================================================================================== me
@router.get(
    "/auth/me",
    response_model=MeOut,
    tags=["Accounts"],
    summary="Who am I",
    description="The caller as the server sees it: a signed-in person, the shared API token or the local machine, with the "
    "role and what it allows. The web interface asks this to decide what to show.",
    dependencies=[Depends(require_signed_in)],
)
def me(request: Request) -> MeOut:
    return _me(current_principal(request))


@router.post(
    "/auth/logout",
    status_code=204,
    tags=["Accounts"],
    summary="Sign out",
    description="Ends the session of the cookie that was sent, on the server, and clears the cookie. Signing out of a "
    "session that has already ended is not an error.",
)
def logout(request: Request, response: Response) -> Response:
    st = state_of(request)
    token = request.cookies.get(SESSION_COOKIE)
    if token and st.accounts is not None:
        st.accounts.revoke_session(token, client=client_of(request))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.post(
    "/auth/password",
    status_code=204,
    tags=["Accounts"],
    summary="Change my password",
    description="Checks the current password, applies the password rules to the new one, and ends every other session of "
    "this account. Needed once after an administrator made or reset the account with a temporary password.",
    dependencies=[Depends(require_signed_in)],
)
def change_password(body: ChangePasswordRequest, request: Request) -> Response:
    principal = current_principal(request)
    if principal.kind != "user" or principal.user_id is None:
        raise UserError("the shared API token and the local machine have no password to change")
    st = state_of(request)
    _service(st).change_password(
        principal.user_id,
        body.current_password,
        body.new_password,
        keep_session=principal.session_id,
        client=client_of(request),
    )
    return Response(status_code=204)


# ============================================================================================== administration
@router.get(
    "/users",
    response_model=list[AccountOut],
    tags=["Accounts"],
    summary="List accounts",
    description="Every account with its role and status, oldest first. Administrators only.",
    dependencies=[Depends(require_admin)],
)
def list_users(request: Request) -> list[AccountOut]:
    return [_account(u) for u in _service(state_of(request)).list_users()]


@router.post(
    "/users",
    response_model=TemporaryPassword,
    status_code=201,
    tags=["Accounts"],
    summary="Create an account",
    description="Makes an account with the role given. With no password a temporary one is made and returned once; either "
    "way the person must change it at the first sign-in. Administrators only.",
)
def create_user(
    body: CreateUserRequest, request: Request, admin: Principal = Depends(require_admin)
) -> TemporaryPassword:
    user, temporary = _service(state_of(request)).create_user(
        admin.label,
        body.username,
        role=body.role,
        password=body.password,
        display_name=body.display_name,
        email=body.email,
        client=client_of(request),
    )
    return TemporaryPassword(account=_account(user), temporary_password=temporary)


@router.patch(
    "/users/{user_id}",
    response_model=AccountOut,
    tags=["Accounts"],
    summary="Change an account",
    description="Changes the role, the status (approve a pending account, disable or enable one) or the display name. A "
    "role or status change ends the account's sessions. The last active administrator cannot be demoted or disabled.",
)
def update_user(
    user_id: Annotated[str, Path(description="Id of an account, from `GET /users`")],
    body: UpdateUserRequest,
    request: Request,
    admin: Principal = Depends(require_admin),
) -> AccountOut:
    user = _service(state_of(request)).update(
        admin.label,
        user_id,
        role=body.role,
        status=body.status,
        display_name=body.display_name,
        client=client_of(request),
    )
    return _account(user)


@router.post(
    "/users/{user_id}/reset-password",
    response_model=TemporaryPassword,
    tags=["Accounts"],
    summary="Reset a password",
    description="Sets a temporary password, returned once, ends the account's sessions and unlocks it. The person must "
    "change the password at the next sign-in. There is no e-mail: hand it over yourself.",
)
def reset_password(
    user_id: Annotated[str, Path(description="Id of an account, from `GET /users`")],
    request: Request,
    admin: Principal = Depends(require_admin),
) -> TemporaryPassword:
    svc = _service(state_of(request))
    temporary = svc.reset_password(admin.label, user_id, client=client_of(request))
    return TemporaryPassword(account=_account(svc.get(user_id)), temporary_password=temporary)


@router.post(
    "/users/{user_id}/unlock",
    response_model=AccountOut,
    tags=["Accounts"],
    summary="Unlock an account",
    description="Clears the lock that repeated wrong passwords set, so the person can try again at once.",
)
def unlock_user(
    user_id: Annotated[str, Path(description="Id of an account, from `GET /users`")],
    request: Request,
    admin: Principal = Depends(require_admin),
) -> AccountOut:
    return _account(_service(state_of(request)).unlock(admin.label, user_id, client=client_of(request)))


@router.get(
    "/users/events",
    response_model=list[AccountEventOut],
    tags=["Accounts"],
    summary="Account audit trail",
    description="The most recent registrations, sign-ins (and failures), locks, role and status changes, password changes "
    "and resets. Contains no password, hash or token. Administrators only.",
    dependencies=[Depends(require_admin)],
)
def account_events(
    request: Request, limit: Annotated[int, Query(description="Largest number of events to return (at most 500)")] = 100
) -> list[AccountEventOut]:
    rows = _service(state_of(request)).events(limit=max(1, min(limit, 500)))
    return [AccountEventOut(**r) for r in rows]
