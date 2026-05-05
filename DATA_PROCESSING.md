# Data Processing Addendum Template

This template is for operators offering IStore to organizations. Adapt it with
legal counsel before use.

## Roles

The customer is the controller for uploaded files and derived metadata. The
operator is the processor. IStore contributors are not processors unless they
operate the hosted service.

## Processed Data

IStore may process account identifiers, uploaded files, served variants,
thumbnails, optional EXIF/GPS metadata, optional AI summaries, optional semantic
embeddings, optional face crops, optional face embeddings, consent records,
cloud-file mappings, and audit records.

## Purpose

The data is processed to store, organize, search, preview, summarize, and delete
user files according to user consent and operator configuration.

## Security Measures

Required measures include TLS, Redis-backed rate limiting, file validation
before persistence, object storage encryption, separate biometric encryption
scope, Postgres volume encryption, encrypted backups, append-only audit logs,
role-based access control, signed download URLs, and RLS for biometric tables.

## Deletion

Image and account deletion remove primary objects, derived objects, derived
rows, and user-specific learning state. Audit rows remain as legal and security
records. Backups expire according to the documented retention period.

## Subprocessors

List hosting providers, email providers, object storage, database services, KMS,
and any AI model providers used by the hosted deployment.
