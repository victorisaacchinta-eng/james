---
doc_id: DEMO-POLICY-PREREQUISITES
version: v1
title: "Demo policy: prerequisites and the evidence each one needs"
applies_to_category: centrifugal_pump
label: "Synthetic training material. Not an OEM procedure."
---
# Demo policy: prerequisites and the evidence each one needs

Synthetic training material. These rules exist to exercise the software. They are not plant
safety rules. WARDEN enforces them from `rules/lab_pump_v1.yaml`; this page explains them.

## ALLOW-00 Allowlisted actions only {#POL-ALLOW-00}

The Lab only shows steps. It has no action that writes to a machine controller.

## EVID-01 Every step has a source {#POL-EVID-01}

A step with no linked evidence or procedure section is blocked.

## ISO-01 Isolation before guarded or pressure-boundary work {#POL-ISO-01}

Steps that open a guard or a pressure boundary need a recorded demo isolation confirmation
for the same job, asset and guidance version. Required evidence: an operator attestation.

## PPE-01 PPE before hands-on work {#POL-PPE-01}

Hands-on steps need a recorded demo PPE confirmation. Required evidence: an operator attestation.

## TEMP-01 No hand contact above the demo touch limit {#POL-TEMP-01}

Hand contact with the bearing housing is blocked while the latest logged bearing temperature
is above 55 C (a demo value, not an engineering limit). If the temperature is not available,
WARDEN asks for it rather than assuming it is safe.
