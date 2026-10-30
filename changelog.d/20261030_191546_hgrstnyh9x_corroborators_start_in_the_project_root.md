### Fixed

- **A corroborator runs in the project root, wherever `trellis` was invoked
  from (#101).** It used to inherit the caller's working directory, so a
  relative script path in `trellis.toml` worked only from the root, and a
  corroborator that reads the repository checked a different tree depending
  on where you stood. The snapshot payload is unchanged and still carries no
  path.
