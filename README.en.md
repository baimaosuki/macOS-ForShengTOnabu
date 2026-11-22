# macOS on Xiaomi Pad 5 · nabu

[简体中文](README.md) · [Status](STATUS.md) · [Progress](PROGRESS.md) · [ESP menu](INSTALLING.md) · [Source release](OPEN_SOURCE.md) · [Source map](REPOSITORY.md) · [Build notes](BUILDING.md) · [Credits and licenses](UPSTREAM.md)

An experimental UniXNU-based macOS port for **Xiaomi Pad 5 (nabu, SM8150-AC, Adreno 640)**. The current OS target is **macOS 27.3 / 26C5035r**. This repository is an independent nabu integration maintained by baimaosuki; see the upstream acknowledgements.

> **Development paused — 2026-11-22.** USB does not work, touch works, GPU operation is suspected but unconfirmed, and many drivers remain missing or incomplete. No restart date is set.

| Area | Current device status |
| --- | --- |
| Boot and display | Boots to a visible system interface; experimental |
| NT36523 touch | Working according to current device feedback; complete multitouch gesture support is unconfirmed |
| USB Host / external keyboard and mouse | Not working; peripheral power is not evidence of usable input |
| Adreno 640 GPU | Suspected to be working; stable hardware acceleration, desktop composition and performance are unconfirmed |
| Other drivers | Multiple missing or unvalidated areas, including audio, wireless, cameras, sensors and full power management |
| Maintenance | Paused; no ETA or next-release commitment |

[STATUS.md](STATUS.md) is the current status record. Build success, published services, fences or visible pixels alone do not establish a working GPU desktop or USB input.

The main integration is in [`devices/nabu/`](devices/nabu/); shared platform code is in [`soc/sm8150/`](soc/sm8150/). The remaining framework and documentation are inherited from UniXNU. `main` is the public source snapshot; historical revisions remain in Git.

The latest USB event-routing change is included in `main`; working mouse input still awaits device verification. The latest driver snapshot is [`bf88431`](https://github.com/baimaosuki/macOS-ForShengTOnabu/commit/bf8843104b08006a94056bc20156926a2c11800b), with successful [native CI 45012322361](https://github.com/baimaosuki/macOS-ForShengTOnabu/actions/runs/45012322361). This is build evidence, not USB or GPU hardware acceptance.

The OS target remains 27.3; SDK26.5 is the compiler SDK used by the existing pipeline. SM8150 lacks hardware PAuth, so this port uses ordinary arm64 with Zeta PAC adaptations. See [BUILDING.md](BUILDING.md) for required local inputs and build limits. Current source does not include Apple OS images, device firmware, private signing keys or local accounts.

Licensing is defined by [LICENSE](LICENSE) and each component's own license. See [UPSTREAM.md](UPSTREAM.md) for attribution.
