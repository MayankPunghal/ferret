# Vendored: libwinpthread-1.dll (Windows x64 only)

Why: the Windows wheel of `pixeltable-pgserver==0.6.0` ships `postgres.exe` linked against the MinGW
runtime `libwinpthread-1.dll` but does not include that DLL. Without it, Windows either fails to start
PostgreSQL or loads an incompatible copy from PATH (`0xC000007B`). `db.start_embedded` copies this file
next to `postgres.exe` on Windows when it is missing or different.

- Source package: https://repo.msys2.org/mingw/ucrt64/mingw-w64-ucrt-x86_64-libwinpthread-14.0.0.r495.g63ebe6cdb-2-any.pkg.tar.zst
  (package SHA-256 0cee80308aec93607165be8c8655c1c3884389e0c2829b022643de7988d8b1f9)
- File: `ucrt64/bin/libwinpthread-1.dll`, SHA-256 817e5b2636f532666e8f255e225af75e9d0c41b141512fde45264070a65b1c92
- Architecture x86-64 (PE machine 0x8664), UCRT; imports only Windows system DLLs.
- `postgres.exe` imports one symbol from it (`clock_gettime`), which this build exports.
- License: MIT (mingw-w64 project) — see `libwinpthread-COPYING.txt`.

Remove this workaround once pixeltable-pgserver bundles the DLL itself.
