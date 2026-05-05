# Terms of Use

IStore is provided under the Apache-2.0 license for software use. Hosted
operators are responsible for the legality of data they accept, store, process,
and delete.

Users must be at least 13 years old unless the operator has implemented a full
parental-consent flow. Registration requires `age_confirmed: true`; accounts
created without that confirmation are rejected by the API.

Do not upload illegal content, malware, credential dumps, or files you do not
have the right to store. Operators may suspend or delete accounts used to attack
the service or violate applicable law.

The service processes biometric data only when the user opts in to the relevant
consent scope. Consent can be withdrawn, which deletes derived biometric data
for that scope.

The software is provided as-is, without warranties. Operators should complete a
security review, configure TLS, Redis rate limiting, encrypted object storage,
encrypted backups, and secret management before public deployment.
