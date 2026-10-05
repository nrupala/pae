# Shamir 3-of-5 — test reproduction

Source under test: `ui/src/crypto/shamir.ts` @ branch `wright/pae-p4-shamir` (commit 0483fa0a).

```bash
cd /tmp && rm -rf shamir-repro && mkdir shamir-repro && cd shamir-repro
# 1. get a TS compiler (cached at /tmp/p4-shamir/tsbin if the VM session persists)
npx --yes -p typescript@5.4.5 tsc --version
# 2. extract the file from the branch tarball
python3 ~/workspace/skills/github/bin/gh_tarball.py nrupala pae wright/pae-p4-shamir pae.tgz
tar xzf pae.tgz && mv nrupala-pae-* pae
# 3. compile just shamir.ts (no imports; DOM lib for crypto.getRandomValues typing)
npx --yes -p typescript@5.4.5 tsc --target ES2022 --module ES2022 --moduleResolution bundler \
  --lib ES2022,DOM --strict --noUnusedLocals --noUnusedParameters \
  --outDir out pae/ui/src/crypto/shamir.ts
# 4. run the suite (copy shamir.test.mjs next to ./out)
cp ~/workspace/pae-p4/shamir-tests/shamir.test.mjs .
node --test shamir.test.mjs
# 5. full UI project typecheck
npx --yes -p typescript@5.4.5 tsc -p pae/ui/tsconfig.json --noEmit
```

Optional cross-implementation check (needs network; NOT part of the committed suite):
```bash
# downloads audited privy-io shamir-secret-sharing 0.0.4 to ./oracle-lib, then:
cp ~/workspace/pae-p4/shamir-tests/cross-check.mjs .
node cross-check.mjs   # expect: CROSS-CHECK PASS: 10/10 rounds, both directions
```

KAT oracle (regenerates the hard-coded known-answer vector independently):
```bash
cp ~/workspace/pae-p4/shamir-tests/kat-oracle.mjs .
node kat-oracle.mjs
```
