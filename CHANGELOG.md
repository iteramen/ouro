# Changelog

What each release of this plugin changed for a repository that installs it, newest first.
Consumers pin the plugin by tag, so a release is the unit a person or a resolver reads before
moving a pin. One bullet per landing; an entry names no issue or pull request number; the
`chore(release)` bumps themselves are not entries.

A `**Consumer contract:**` line marks an entry that moves something a consumer's own files
name: a skill's invocation flags or its credential and tool requirements, a table or key of the
binding schema (or a value the binding check newly refuses), a script's parameters or a python
tool's usage lines, the shape of a template a consumer copied, or an Action's own definition file
a consumer's workflow uses at a pinned tag. The line names the old form, the new form and the
one-line migration. A release carrying any such entry takes a minor version bump; every other
release takes a patch.

## [UNRELEASED]

## [v0.2.0] - 2026-10-08

- `actions/gates/action.yml`: the step's comment no longer names the workflow of the repository that
  develops this plugin. No input, output or behavior changes.
- `templates/weekly-pass.yml`, `templates/documentation-rule.md`: a comment and an example no longer
  carry an issue number. No step, key or rule changes; an existing copy needs no edit.
- `README.md`, `templates/weekly-pass.yml`, `templates/docs-freshness.yml`, `templates/ci.yml`: the
  plugin is installed from the public repository `iteramen/ouro`, which takes issues, not pull
  requests. **Consumer contract:** the old form is the weekly-pass plugin checkout of
  `BoJl4apa/ouro` with `token: ${{ secrets.OURO_READ_TOKEN }}`, and the `uses:` pins of
  `BoJl4apa/ouro/actions/docs-freshness@<tag>` and `BoJl4apa/ouro/actions/gates@<tag>`; the new form
  is `iteramen/ouro` for the checkout's `repository:` and for both `uses:`, with no `token:` input.
  Migration in a copied workflow: re-point `repository:` and every `uses:`, and delete the `token:`
  line before the `OURO_READ_TOKEN` secret, because actions/checkout reads `token` as a required
  input and an emptied secret fails the checkout.
  The public repository's tags start at v0.2.0, so `OURO_REF` and each `@<tag>` pin move to v0.2.0 or
  later: no v0.1.x tag exists on `iteramen/ouro`, and a pin left at one fails the checkout and the `uses:`.
