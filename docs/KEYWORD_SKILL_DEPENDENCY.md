# Keyword evidence dependency boundary

The pure `domains/content_operations/tiktok_keyword_evidence.py` module is retained from commit `b97877644f25e958eda8ec5de3f6290b54cc4903`, without importing that commit's other changes. Its only dependencies are the Python standard library. It builds request descriptions and reduces supplied responses; it does not call an API, read credentials, approve facts, write a product state, or publish.

Official keyword suggestions remain unverified facts until selected and fact-verified. Inherited evidence remains explicitly inherited and does not claim an official read of the follower shop. These contracts are covered by offline synthetic tests.

The keyword-read CLI is now also retained from that exact source commit. Its offline tests mock credentials, the official diagnosis read, and product-state persistence; they cover explicit read opt-in, stale-revision rejection before credentials, bounded direct versus inherited evidence, and preservation of unverified product facts. The CLI performs official API reads and local state writes when explicitly invoked. This package has not invoked it against real state or providers.

Six additional personal R1 scripts remain absent from this candidate's four-script R1 directory. Three installed copies differ from their latest committed sources; other helpers require review of approval semantics or candidate-state identity before exposing them. Do not overwrite the richer personal R1 installation. Source inventories must be reconciled file by file before installing.

The two publication Skill registration corrections retain existing source unchanged: SKILL.md equals `153912b0623bda986a61dc7012e6f36a96362dd0`; its bundled publication test equals `7cedebc6ac0d2b9fbdfadfb08a77e87861a52f53`. Previously recorded hashes matched the parents of those exact changes. Selected R1/R2/R3 registered files are hash-consistent after repair; that is not proof of complete personal Skill portability or live publication readiness.
