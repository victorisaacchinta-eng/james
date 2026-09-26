# JAMES Industry Pack Format (james-pack/0.1)

Status: draft, Step 1 of the build. Open spec (Apache 2.0 per the commercialisation plan).
Signing is Step 2 and is not part of this version yet.

## What a pack is

A pack is one folder that carries everything JAMES needs to support a class of machines:
the safety rules, the tests for those rules, the fault vocabulary, the sensor channels,
and the manual. An OEM ships a pack; the plant runs one JAMES app across packs.
The app code never hardcodes a machine. If it is about a machine, it comes from a pack.

## Folder layout

```
packs/<pack-id>/
  manifest.yaml                 required; the only file the loader reads first
  rules/<suite>.yaml            WARDEN rules (deterministic, no AI)
  rules/<suite>.cases.yaml      seeded safety cases for those rules
  faults/<class>.yaml           causes, manual sections, symptom words, sensor signatures
  sensors/<class>.yaml          sensor channels, units, which ones feed WARDEN facts
  docs/<manual>.pdf             the manual PAGE searches and cites
  data/<file>.csv               sample logs (demo only; Step 4 replaces with live adapters)
```

Names are a convention. The manifest is what counts: every file must be listed there.

## manifest.yaml

| Field | Type | Meaning |
|---|---|---|
| `format` | string | Must be `james-pack/0.1`. Unknown formats are refused. |
| `id` | slug | Lowercase letters, digits, hyphens. Must equal the folder name. |
| `version` | semver | `MAJOR.MINOR.PATCH`, e.g. `0.1.0`. |
| `name` | string | Human name shown on the HUD. |
| `publisher` | string | Who built it. Demo packs must say they are not from an OEM. |
| `status` | enum | `demo`, `pilot` or `approved`. |
| `licence` | string | SPDX id or `proprietary`. |
| `equipment_classes` | map | Class id to its files (see below). At least one. |
| `assets` | list | Demo site binding: which physical machines exist and how they are tagged. |
| `files` | map | Every file in the pack, relative path to its SHA-256. Must be the last key. |

Unknown fields anywhere in the manifest are refused, so a typo cannot silently disable a rule.

### equipment_classes.<class>

| Field | Required | Meaning |
|---|---|---|
| `label` | yes | e.g. "Centrifugal pump" |
| `rules` | yes | path to the WARDEN rules file |
| `rule_tests` | yes | path to the seeded cases; the pack check runs them |
| `faults` | yes | path to the fault file |
| `sensors` | yes | path to the sensor file |
| `manual` | yes | path to the manual PDF |

### assets[]

| Field | Required | Meaning |
|---|---|---|
| `asset_id` | yes | e.g. `P-3`. Unique in the pack. |
| `class` | yes | one of `equipment_classes` |
| `label` | yes | shown on screen and in seeded cases |
| `aruco_id` | no | ArUco DICT_4X4_50 marker id on the asset tag. Unique. |
| `sample_log` | no | path to a sample CSV for this asset |

Note: `assets` is plant data, not OEM data. It lives in the pack for the demo and will move to a
separate site file once there is more than one site (Step 10, fleet memory).

## Loader rules (all fail closed)

The loader (`james_core/pack.py`) refuses the whole pack, listing every problem, if any of these fail:

1. `manifest.yaml` is missing, is not valid YAML, or does not match the schema above.
2. `format` is not supported, or `id` does not match the folder name.
3. A file path is absolute, contains `..` or a backslash, or points through a symlink.
4. A file listed in `files` is missing, or its SHA-256 does not match.
5. A file in the folder is not listed in `files` (dotfiles such as `.DS_Store` are ignored).
6. A class or asset points at a file that is not in `files`.
7. Two assets share an `asset_id` or an `aruco_id`, or an asset names an unknown class.
8. The pack says `approved` but one of its rules files does not say `status: approved`.

Hashes give tamper detection today. Step 2 adds an ed25519 signature over `manifest.yaml`,
and since the manifest pins every file's hash, one signature covers the whole pack.

## Choosing the pack

The app loads one pack, `pharma-utility` by default. Override with the environment variable
`JAMES_PACK=<pack-id>`. The HUD top bar shows the pack id, version and status.

## Tools

```
python -m james_core.pack check packs/pharma-utility    # validate + run every class's seeded cases
python -m james_core.pack rehash packs/pharma-utility   # after editing a pack file (dev only)
```

`rehash` rewrites only the `files:` block. In Step 2 it will refuse to run on signed packs;
changing a signed pack means re-signing it in OEM Studio (Step 5).

## Not in this version

Signatures (Step 2), a second equipment class (Step 3), live sensor adapters (Step 4),
licence checks (Step 6), vision models inside packs (Step 7), rule sign-off records (Step 9).
