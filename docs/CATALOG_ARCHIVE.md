# Internal catalog retention

The SKU directory combines current channel-cache identities with a durable
`catalog_archive_members` table. Channel listing status does not determine
whether an internal product exists. A later cache clear cannot remove its
archived identity, source image, or recorded cost evidence.

`catalog_archive.import_members` imports a frozen local evidence packet with
an exact digest, source reference and timestamp. It is not a marketplace
adapter or a new UI maintenance endpoint. It writes only its own archive
table in one SQLite transaction; it neither publishes nor modifies channel
tables, legacy costs, current manual costs, or cost history. Repeating the
same packet is idempotent; conflicting identity documents fail atomically.

Current channel rows take precedence over identical archived identities.
An identity uses platform, shop, product and variant together. Internal SKU
normalization remains the existing bounded alias rule. A missing SKU is
retained under its exact identity and is not assigned an invented SKU.

Records can come from an official product readback, an existing local
catalog snapshot, or a real approved internal Product Center record.
Internal-only records use their actual Product Center offer and source SKU
key and never receive fabricated marketplace IDs. Source kind, timestamp,
product status and variant status remain available in the directory result.
The name cell shows archive status; its source timestamp is available in
the tooltip. A historical observation is not a current selling-state claim.

Manual canonical costs retain priority. Recorded legacy costs may be
preserved as explicitly attributed archive cost evidence so removal of a
channel cache does not erase their provenance. Image reads use only URLs
registered in current or archived catalog records and retain the existing
public-HTTPS and response validation rules. Existing logistics weights have
priority. Archive fallback accepts only positive finite values with an explicit
matching `weight_kg`/`kg` or `weight_g`/`g` source pair. Conflicting archive
weights remain unresolved; original logistics tables are never modified.

For a recovery, freeze provider identity/variant evidence and scope first,
verify the packet on a separate copy, back up the current review database,
then import that packet with the sole writer. Compare every original table
before and after. Do not replace the review database with a stale copy or
lose user edits made during capture. Missing access and incomplete page
coverage remain unverified, never proof that a product does not exist.
