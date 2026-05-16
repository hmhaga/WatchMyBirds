# Security Policy & Appliance Hardening

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.x     | :white_check_mark: |

Only the latest release receives security updates. We recommend always running the most recent version.

## Reporting a Vulnerability

We take the security of this project seriously. If you discover a security vulnerability, please report it privately.

**Do not open public issues for security vulnerabilities.**

**How to report:**
1. **GitHub Security Advisories (Preferred):** Use the "Report a vulnerability" button in the Security tab of this repository.
2. **Email:** Contact the repository maintainer directly via their GitHub profile.

**Response Commitment:** We aim to acknowledge reports within 48 hours and provide a fix or mitigation plan within 7 days for critical issues.

---

## Raspberry Pi Appliance Security
The WatchMyBirds Raspberry Pi image is designed as a secure-by-default appliance. It implements strict system hardening measures to ensure suitability for exposed environments.

### 1. User & Authentication
- **No Default User:** The standard `pi` user is completely removed.
- **Dedicated Service User:** The application runs as a dedicated system user `watchmybirds` with:
  - No login shell (`/usr/sbin/nologin`).
  - No home directory suitable for interactive use.
  - Minimal group privileges (`video`, `gpio`, `plugdev`).
- **Web UI Authentication:** 
  - The Web Interface is protected by a password (`EDIT_PASSWORD`) loaded from `/etc/app/app.env` or `settings.yaml`.
  - Default is empty unless configured; an empty password currently allows login and should be avoided on real networks.
  - Flask sessions use `FLASK_SECRET_KEY`; if unset, a static dev key is used and should be overridden in production.
- **Root Locked:** The `root` account is locked (`passwd -l root`) and has no password access.
- **No Interactive Access:** There are no interactive users enabled by default.
- **Dev Images (Non-Production):** `build-dev` images relax access controls for local debugging and faster iteration.

### 2. Environment Differences (Prod vs Dev)
| Feature | Production (`app.service`) | Development (`app-dev.service`) |
| :--- | :--- | :--- |
| **Filesystem** | Read-Only (`ProtectSystem=strict`) | Writes allowed to `/opt/app` |
| **Home Access** | Blocked (`ProtectHome=yes`) | Allowed (`ProtectHome=yes` with exceptions or disabled) |
| **Privileges** | `NoNewPrivileges=true`, SUID blocked | `NoNewPrivileges=false`, relaxed for debugging |
| **Power Management** | Via Polkit/logind (no sudo) | Via Polkit/logind (no sudo) |
| **Admin User** | Locked | Unlocked with elevated local debugging access |
| **SSH** | Disabled | Enabled by default |

> [!WARNING]
> **Dev Image Risk:** The development image relaxes several hardening controls for rapid iteration and debugging. Never deploy a Dev image in a production environment.

### 3. Network Security
- **SSH Disabled by Default:** SSH is disabled on the image. It must be explicitly enabled by the user.
- **SSH Hardening (When Enabled):**
  - `PermitRootLogin no`
  - `PasswordAuthentication no` (Public Key Authentication ONLY)
  - `X11Forwarding` and `AgentForwarding` disabled.
- **Unique Host Keys:** SSH host keys are deleted during the build and regenerated when SSH is enabled (via `/boot/firmware/ssh` on first boot or later).
- **Firewall (UFW):**
  - **Default Policy:** Deny Incoming, Allow Outgoing.
  - **Web Interface:** Port 80/tcp allowed.
  - **AP Services:** DNS/DHCP restricted strictly to the `wlan0` interface.
  - **Enforcement:** UFW is configured and enabled by the first-boot script.
- **Fail2Ban:** Not installed by default in the appliance image.

### 4. System Isolation & Hardening
- **Systemd Sandboxing:** The main application service (`app.service`) runs with maximum security directives:
    ```ini
    ProtectSystem=strict          # Read-only filesystem view (CRITICAL)
    ReadWritePaths=/opt/app/data /var/log/app
    WorkingDirectory=/opt/app
    PrivateTmp=true               # Isolated /tmp
    NoNewPrivileges=true          # SUID binaries blocked (sudo cannot escalate)
    ProtectHome=yes               # No access to /home
    ProtectKernelTunables=true    # No sysctl modifications
    ProtectKernelModules=true     # No module loading
    ProtectKernelLogs=true        # No kernel log access
    ProtectControlGroups=true
    ProtectClock=true
    RestrictNamespaces=true       # Container escape prevention
    MemoryDenyWriteExecute=true   # Prevent memory modification (W^X)
    LockPersonality=true          # Lock kernel execution domain
    RestrictRealtime=true
    RestrictSUIDSGID=true         # Block SUID/SGID bit execution
    ```
  - **Dev Mode:** `app-dev.service` relaxes sandboxing to allow local updates and debugging workflows.

- **Power Management (Polkit/logind):**
  - Reboot/Shutdown from the Web UI uses `systemctl` via DBus, not `sudo`.
  - A Polkit rule (`/etc/polkit-1/rules.d/10-watchmybirds-power.rules`) grants the `watchmybirds` user permission to call `org.freedesktop.login1.reboot` and `power-off`.
  - This design is compatible with `NoNewPrivileges=true` and avoids SUID-based privilege escalation.

### 5. First-Boot Hardening
- **Partition Expansion:** The filesystem is automatically expanded on first boot to use all available SD card space.
- **User Provisioning:** All interactive users are locked; only the dedicated `watchmybirds` service user is active.
- **SSH Host Key Generation:** Host keys are regenerated on first SSH enable to ensure uniqueness.
- **Firewall Configuration:** UFW is set up and enabled with sensible defaults (deny incoming, allow outgoing).

---

## Vulnerability & Incident Response

We maintain a security-conscious development process:

1. **Dependency Monitoring:** We regularly update Python dependencies, Docker base images, and system packages to patch known vulnerabilities.
2. **Code Review:** Security-sensitive changes are peer-reviewed before merging.
3. **Container Image Scanning:** Published Docker images are scanned for known CVEs before release.
4. **Testing:** Security hardening is validated in CI/CD pipelines before release builds.

---

## Additional Security Resources

For more information on security topics related to WatchMyBirds:

- **[Privacy Policy](docs/PRIVACY.md)** — What data is collected and how it is handled.
- **[Architecture](docs/ARCHITECTURE.md)** — System design and threat model considerations.
- **[Configuration](docs/CONFIGURATION.md)** — Secure configuration best practices.
