import { useEffect } from "react";

/** While `dirty`, ask before the person leaves this page: closing or reloading the tab (the browser's own question), and
 * following a link inside the interface (a confirmation of ours). The browser's back button cannot be asked: a hash router
 * gives a page no way to refuse it, which is why a draft is also kept in the tab. */
export function useUnsavedChanges(dirty: boolean, message = "You have changes that are not saved. Leave this page and lose them?"): void {
  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    const onClick = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
      const target = event.target instanceof Element ? event.target : null;
      const anchor = target?.closest("a[href]");
      if (!anchor || anchor.getAttribute("target") === "_blank" || anchor.hasAttribute("download")) return;
      const href = anchor.getAttribute("href") ?? "";
      if (!href.startsWith("#/") && !href.startsWith("/")) return; // a link inside the interface, not out of it
      if (window.confirm(message)) return;
      event.preventDefault();
      event.stopPropagation();
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", onClick, true); // capture: before the router sees the click
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, [dirty, message]);
}
