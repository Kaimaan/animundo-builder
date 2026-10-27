# The fox pack fixture

A home's real pack: `jobs/review-fox` replayed intake to pack
(#52.1), copied here so the headless check installs exactly what
the builder wrote. Derived, not verbatim: the local job name
`review-fox` is not a legal draft folder (the game's pattern is
`[a-z0-9_]+`; real draft ids never hyphenate), so the ids are
`review_fox` / `draft_review_fox` and the manifest hash is
recomputed. Everything else is byte-identical.
