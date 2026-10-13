import { FeedbackSupportMenu } from "../../components/feedback/FeedbackSupportMenu";
import { Card, PageHeader } from "../../components/ui";

export function FeedbackHome() {
  return (
    <>
      <PageHeader title="Feedback & Support" subtitle="Ask for something new, tell us what went wrong, and follow what happens to it." />
      <div className="stack">
        <FeedbackSupportMenu />
        <Card title="Good to know">
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            <li>You can send a feature request or an issue report with only a title and a few words. The rest helps, and none of it has to be invented.</li>
            <li>
              Files and diagnostic information are optional. Secrets that AgentLab recognises are masked, but masking is not a guarantee: do not
              paste passwords or keys.
            </li>
            <li>What you send is saved on this server. You can leave and come back: your submissions are listed under &ldquo;My submissions&rdquo;.</li>
          </ul>
        </Card>
      </div>
    </>
  );
}
