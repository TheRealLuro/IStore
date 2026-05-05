# IStore — Face Recognition Disclosure (v1)

**Effective date:** 2026-04-30
**Policy version:** v1

This document is shown to every IStore user before face recognition is enabled
on their account. By granting consent, the user agrees to the terms below.

## What we collect

When face recognition is enabled, IStore processes images you have uploaded
to detect human faces in them. For each detected face, IStore stores:

- The location of the face within the image (a bounding box).
- A 512-dimensional numerical representation of the face (a "face embedding"
  or "face template"). This is biometric data under both the EU General Data
  Protection Regulation (GDPR Article 9) and the Illinois Biometric
  Information Privacy Act (BIPA, 740 ILCS 14/).
- A small cropped image of the face, kept only so you can review which
  faces have been grouped together.

We do **not** collect or store any face data unless and until you explicitly
enable face recognition.

## Why we collect it

Face data is used **only** to let you sort and search your own photo library
("show me photos of Mom"). It is **never**:

- Shared with any third party.
- Used to train any shared machine-learning model.
- Compared against another IStore user's data.
- Sold, rented, leased, or otherwise transferred to anyone.

Each user's face data is isolated to that user's account.

## How long we keep it

- Face data is kept for as long as you keep face recognition enabled, up to
  three (3) years from your last activity, whichever comes first (BIPA
  §15(a) retention requirement).
- If you disable face recognition, all bounding boxes, face embeddings,
  cropped face images, and named-person records for your account are
  permanently deleted within twenty-four (24) hours. Your underlying photos
  are retained.
- If you delete your IStore account, all face data is permanently deleted
  along with your other account data within thirty (30) days (GDPR Art. 17
  deletion-on-request requirement).

## Your rights

You may at any time:

- **Withdraw consent** from your settings. Your face data is then deleted
  on the schedule above.
- **Request a copy** of your face data through the account-export endpoint.
- **Request deletion** of specific named people, individual face detections,
  or all face data, separately from withdrawing consent.
- **Contact us** with questions about how your biometric data is handled.

## Records of consent

Each grant or withdrawal of consent is recorded in an append-only consent
log together with:

- The version of this policy you agreed to.
- The SHA-256 hash of this exact text, so we can prove which version you
  saw.
- Your typed signature.
- Timestamp, IP address, and browser user-agent.

This satisfies BIPA §15(b)'s "written consent" requirement via electronic
signature.

## To grant consent

By checking both consent boxes and typing your full legal name as a
signature, you confirm that you:

1. Have read and understood this disclosure.
2. Consent to the collection of your biometric face data as described.
3. Consent to the retention period stated above.
4. Understand that you can withdraw consent at any time.
