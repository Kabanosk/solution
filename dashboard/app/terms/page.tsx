import type { Metadata } from "next";
import { LegalDocument } from "@/components/legal-document";

export const metadata: Metadata = {
  title: "Terms of Service · Scout",
  description: "The terms for using the Scout finance workspace.",
};

export default function TermsPage() {
  return <LegalDocument title="Terms of Service">
    <section id="scout-terms-operator" className="space-y-3">
      <h2 className="text-xl font-semibold">Operator and service</h2>
      <p>Scout is a personal demonstration project operated by Piotr Warzachowski. Questions about these terms can be sent to <a className="underline underline-offset-4" href="mailto:warzachowskip@gmail.com">warzachowskip@gmail.com</a>.</p>
      <p>These terms apply when you use Scout&apos;s organization workspaces, finance-agent features, and Google sign-in. By using Scout, you agree to these terms and the <a className="underline underline-offset-4" href="/privacy">Privacy Policy</a>.</p>
    </section>
    <section id="scout-terms-accounts" className="space-y-3">
      <h2 className="text-xl font-semibold">Accounts and permissions</h2>
      <p>Keep your account credentials secure and use only organizations and data you are authorized to access. A Google account does not automatically grant membership in an existing Scout organization. Organization owners are responsible for deciding who may use their workspace and for obtaining permission before adding personal or business information to it.</p>
      <p>Do not attempt to access another organization&apos;s data, bypass security controls, interfere with the service, or use Scout in violation of applicable law or third-party terms.</p>
    </section>
    <section id="scout-terms-integrations" className="space-y-3">
      <h2 className="text-xl font-semibold">Integrations and user instructions</h2>
      <p>Scout may use information and integrations that an organization configures to perform its requested workflows. You are responsible for reviewing outputs, approvals, and any actions before relying on them. Google sign-in only confirms your identity: it does not grant Scout access to Gmail. Mailbox onboarding is not available in this release.</p>
    </section>
    <section id="scout-terms-finance" className="space-y-3">
      <h2 className="text-xl font-semibold">Demonstration service, not financial advice</h2>
      <p>Scout may generate incomplete or incorrect summaries, forecasts, and recommendations. It is not an accountant, lawyer, financial adviser, bank, or payment service. Review important results with a qualified professional and your original records. Do not rely on Scout to execute a real financial transaction or to preserve your only copy of a record.</p>
      <p>The current hosting environment is temporary. Availability, continued access, and preservation of demonstration data are not guaranteed. The service may change or be discontinued; export information you need to keep outside Scout.</p>
    </section>
    <section id="scout-terms-changes" className="space-y-3">
      <h2 className="text-xl font-semibold">Changes and contact</h2>
      <p>These terms may change as the service changes. The current version and effective date will remain available on this page. If you no longer agree, stop using Scout and contact us about account or data requests at the address above.</p>
    </section>
  </LegalDocument>;
}
