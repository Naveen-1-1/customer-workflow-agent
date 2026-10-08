# Retail store data

Copied unmodified from [sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench)
at commit `5bfa7e37b36656b37dc6d022156be6563c1007f3`, path `data/tau2/domains/retail/`.
MIT License, Copyright (c) 2025 Sierra Research — see `LICENSE.tau2-bench`.

| File | What it is | sha256 |
|---|---|---|
| `db.json` | The store: 50 products (591 variants), 500 users, 1000 orders | `413a65160adbdb5fde0ffc0015c49b6d70250b10c18128de169b597af7766765` |
| `policy.md` | The retail agent policy our code enforces | `2c9652afbce57d6e087768d37cda64d31c53d50b3e3225cfdb791bac66466467` |
| `tasks.json` | 114 customer scenarios, used as test cases | `8e03ebce7901bd6218e7a7dc3105faa9324091a68058f7fe61c65262868812e8` |

These files are never modified at runtime: the app works on a copy in `var/db.working.json`.
