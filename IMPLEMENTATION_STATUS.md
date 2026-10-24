# Wallet security and customer operations

Release checklist and operational notes for wallet security and analytics.

## Implemented

- Owner wallet freeze stored in the database, developer-only toggle and audit record.
- Freeze checked by payout creation and execution.
- Provider balance displayed separately and checked before immediate payouts.
- UGX 1,500 withdrawal fee allowance and maximum-withdrawal UI.
- Internal pending payout fee allowance and provider outstanding payout allowance.
- Password-verified email OTP, amount/session-bound, five-minute expiry, five attempts, 60-second resend delay, single use.
- Owner recipient list includes both security contacts; developer uses developer contact.
- Twenty-minute inactivity and eight-hour absolute privileged-session timeouts.
- Developer list of all users with customer block/restore and privileged-account protection.
- Owner customer list excludes developers.
- Public shell hidden on developer routes; role-specific mobile account shortcut.
- Customer payment-attempt history, receipt link and retry button in account view.

## Operations

- SMTP SSL authentication and test submission to both security contacts verified on port 465. Inbox placement cannot be established from an SMTP acceptance response.
- Apply the accounts and catalog migrations before serving the new application.
- Schedule `deliver_security_notices` every five minutes to retry failed alert delivery.
- Schedule `cleanup_operational_data` daily; old performance samples and OTP challenges expire, while financial and access histories remain.
- Run `optimize_package_images` once after migration. New package uploads optimize automatically; source images remain intact.

## Additional implemented features

- Database-backed OTP challenges with locked single consumption and five-attempt limits.
- Login alert delivery, queued retry and active-session/device history with remote revocation.
- Password/recovery-code rotation and verified payout destination changes from Security.
- Customer access-event history, closing countdowns, purchase history and preference persistence.
- Funnel collection and package/device/browser/hour/customer-type reports. MTN/Airtel reports use provider-returned network data; unknown remains explicit.
- Web Vitals LCP/INP/CLS collection and device-specific p75 reports; Lighthouse reports in CI.
- Responsive Security and Insights interfaces using BK's existing design system.

## Measurement limits

- Tracking begins with this release; historical visits and betslip opens cannot be reconstructed.
- Relworx does not expose a separate PIN approval signal in the current integration. Successful payment confirms approval; no separate approval time is invented.
- Browser metrics are observational, may be suppressed by privacy preferences and require real traffic. Lighthouse uses a documented empty-catalogue fixture because CI has no backend.
- Lighthouse timing budgets warn and publish reports; they do not claim that production p75 targets have already been achieved. INP is measured in the field; Lighthouse blocking time is a lab proxy.
- The UGX 1,500 fee is the configured allowance supplied by the owner. Actual provider charges take precedence when returned on successful settlement.

No live transfer is needed to verify this release. Test notifications have been submitted to the authorized recipients.
