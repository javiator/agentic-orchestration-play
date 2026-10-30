# Postmortem: Beacon EU Outage, 14 April 2025

## Summary
On 14 April 2025, Beacon's EU region was unavailable for 3 hours and 12
minutes. Customers could not send or query data during that time.

## Root cause
The TLS certificate on the EU ingestion gateway expired. Renewal was a manual
task, and the reminder went to a mailbox nobody monitored.

## Ownership
Postmortem owner: Head of Platform. Incident commander on the day: the
Platform on-call engineer.

## Action items
1. Automate certificate renewal for all gateways — completed in May 2025.
2. Add a certificate-expiry alert in Kestrel that fires 21 days ahead —
   completed in May 2025.
3. Review all other manual renewal tasks — in progress.
