import type { Metadata } from "next";
import { LegalDocument } from "@/components/legal-document";

export const metadata: Metadata = {
  title: "Privacy Policy · Scout",
  description: "How Scout handles account and workspace information.",
};

export default function PrivacyPage() {
  return <LegalDocument title="Privacy Policy">
    <section id="scout-privacy-operator" className="space-y-3">
      <h2 className="text-xl font-semibold">Who operates Scout</h2>
      <p>Scout is a personal project operated by Piotr Warzachowski. For questions about your personal data, access, or deletion, email <a className="underline underline-offset-4" href="mailto:warzachowskip@gmail.com">warzachowskip@gmail.com</a>.</p>
      <p>Scout is a demonstration finance workspace. Its current hosting environment is temporary; do not use it as your only copy of important records.</p>
    </section>
    <section id="scout-privacy-data" className="space-y-3">
      <h2 className="text-xl font-semibold">Information we process</h2>
      <p>When you create or use an account, Scout processes your name, email address, organization membership, login method, and security information such as session identifiers and sign-in attempts. If you choose Google sign-in, Scout receives your verified Google account identifier, name, and email address.</p>
      <p>In an organization workspace, users may enter conversations, transaction and invoice records, operational information, and data from integrations that the organization explicitly configures. Scout also keeps task, approval, and access audit records to show what its agent did and why. Requests may be logged for security and troubleshooting.</p>
    </section>
    <section id="scout-privacy-purpose" className="space-y-3">
      <h2 className="text-xl font-semibold">Why we use it</h2>
      <p>We use this information to authenticate members, provide their organization workspace, answer their requests, perform the finance workflows they initiate, maintain audit trails, prevent abuse, and troubleshoot the service. We do not use Google sign-in data to advertise to you or sell it.</p>
    </section>
    <section id="scout-privacy-google" className="space-y-3">
      <h2 className="text-xl font-semibold">Google sign-in is not Gmail access</h2>
      <p>Current Google sign-in requests only basic identity information (name, email, and profile). Scout verifies the Google identity token and stores a linked account identifier; it does not retain Google access or refresh tokens from sign-in. Signing in does not connect a mailbox or let Scout read your emails.</p>
      <p>Gmail mailbox connections are not currently available in this version. If that feature is introduced, it will require a separate, explicit permission request and an update to this policy before it is offered.</p>
    </section>
    <section id="scout-privacy-storage" className="space-y-3">
      <h2 className="text-xl font-semibold">Storage, sharing, and retention</h2>
      <p>Scout runs on hosted infrastructure and stores account and workspace records in its application database. An organization&apos;s own configured integrations may receive information needed to perform an action requested in that workspace. Hosting and integration providers process data under their own terms and policies. We do not make one organization&apos;s records available to another organization through the application.</p>
      <p>Login sessions expire after approximately 12 hours. Workspace and audit records do not currently have an automatic deletion deadline; they remain until removed through an operational process. You may request access, correction, or deletion using the contact address above. Some records may need to be retained when required by law or for security.</p>
    </section>
    <section id="scout-privacy-choices" className="space-y-3">
      <h2 className="text-xl font-semibold">Your choices and changes</h2>
      <p>You can stop using Scout, disconnect your Google sign-in authorization in your Google account, and contact us to request account or workspace-data deletion. Disconnecting Google at Google does not automatically erase records already stored in Scout. We may update this policy as the service changes; the current version and effective date will remain available on this page.</p>
    </section>
  </LegalDocument>;
}
