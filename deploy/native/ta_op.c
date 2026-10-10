/* ta-op — the drop-first operational exec wrapper for the production image.
 *
 * Installed root-owned 0555 at /usr/local/libexec/ta-op, outside /app and
 * /data (uid 1001 owns /data; a writable directory must never hold a binary
 * that root may one day exec).
 *
 * WHAT IT IS: a CLOSED table of the operational routes this repo already
 * runs through `docker exec`, each reached only after the process identity
 * has been fully retired to uid/gid 1001 with every capability set empty.
 *
 * WHAT IT IS NOT: a command interface. No mode takes, derives or forwards a
 * caller-supplied executable, path, interpreter flag or shell string. The
 * single operand any mode accepts is one environment NAME, validated after
 * the drop. Unknown mode, wrong arity or malformed NAME all fail closed.
 *
 * Two entry identities, one exit invariant:
 *
 *   getuid()==0     managed-bootstrap entry: the owner-split container (PID1
 *                   bootstrap, compose user 0). Require EXACTLY its four caps
 *                   (KILL, SETGID, SETUID, SETPCAP) permitted,
 *                   effective and bounding, with inheritable and ambient
 *                   empty; then run the full retirement — NNP, keepcaps
 *                   clear, ambient clear, whole bounding set dropped,
 *                   setgroups(0), setresgid/setresuid(1001), explicit capset
 *                   zero — and read every one of them back before any
 *                   runtime target is reached.
 *
 *   getuid()==1001  rootless entry (`docker exec --user 1001`, or a
 *                   container started without compose's user 0).
 *                   Nothing is dropped here because nothing is held, so this
 *                   branch is LEGACY-ROOTLESS VERIFICATION and is NOT a
 *                   managed-bootstrap drop receipt. It asserts the identical
 *                   post-drop state and refuses otherwise — strictly stronger
 *                   than the bare `docker exec` it replaces, which trusted
 *                   the posture implicitly.
 *
 *   anything else   refused.
 *
 * Supplementary groups on the legacy branch: permitted only when the list is
 * empty or is exactly the SAME primary gid 1001. Such an entry conveys no
 * authority the in-force setresgid(1001) does not already grant, and /app and
 * /data are group-owned by that primary gid. Any other gid is refused. The
 * root branch still clears supplementaries to empty.
 *
 * No NSS, no dynamic loader, no getpwnam/getgrnam, no locale, no config file,
 * no /app or /data path opened before the identity is retired. Compile fully
 * static in the existing builder stage.
 */
#define _GNU_SOURCE
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/capability.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

#define TA_OP_VERSION "ta-op 1 modes=6"
#define TA_UID 1001
#define TA_GID 1001
#define SELF "/usr/local/libexec/ta-op"
/* The kernel status file the identity readbacks are taken from. Overridable
 * ONLY at compile time, and only so the native regression in
 * deploy/native/NATIVE-TEST-PLAN.md can feed the readback predicates a crafted
 * status file. The production Dockerfile never defines it (asserted by
 * tests/test_ta_op_modes.py), and nothing at runtime — no env var, no argv, no
 * config — can reach it. */
#ifndef TA_STATUS_PATH
#define TA_STATUS_PATH "/proc/self/status"
#endif
/* CAP_KILL(5) CAP_SETGID(6) CAP_SETUID(7) CAP_SETPCAP(8): the bootstrap's set.
 * SYS_ADMIN is never held; the migration's CHOWN/FOWNER/DAC_OVERRIDE live only
 * in its one-shot container. deploy/role_launcher.py ENTRY_CAPS is the same. */
#define MASK 0x1e0ULL
#define REFUSE 78

extern char **environ;

static void fail(const char *where) {
    fprintf(stderr, "TA_OP_REFUSED:%s errno=%d\n", where, errno);
    exit(REFUSE);
}
#define MUST(test, why) do { if (!(test)) fail(why); } while (0)

/* ---- fixed argv table. Parity with deploy/native/ta_op_modes.tsv is
 * asserted by tests/test_ta_op_modes.py; that file is the single source and
 * scripts/check_drop_first_exec.py reads the same rows. ---- */
#define BUILTIN_VERSION 1
#define BUILTIN_ENV_SUMMARY 2

struct mode {
    const char *name;
    int argc;              /* total argv count accepted */
    int builtin;           /* 0 = exec argv, else BUILTIN_* */
    const char *argv[14];  /* NULL-terminated, compile-time constant */
};

static const struct mode MODES[] = {
    {"version", 2, BUILTIN_VERSION, {NULL}},
    {"env-summary", 2, BUILTIN_ENV_SUMMARY, {NULL}},
    {"pulse", 2, 0,
     {"/opt/venv/bin/python", "/app/scripts/mcp_public_canary.py", "--pulse-only",
      "--url", "http://127.0.0.1:8001/mcp", "--timeout", "10", NULL}},
    {"canary", 2, 0,
     {"/opt/venv/bin/python", "/app/scripts/mcp_public_canary.py",
      "--url", "http://127.0.0.1:8001/mcp", "--timeout", "10", NULL}},
    {"printenv", 3, 0, {"/usr/bin/printenv", NULL}},
    {"bwrap-oracle", 2, 0,
     {"/opt/venv/bin/python", "/app/scripts/workspace_bwrap_oracle.py", NULL}},
};
#define N_MODES ((int)(sizeof(MODES) / sizeof(MODES[0])))

/* ---- /proc/self/status readers (no libc identity lookups) ---- */

static void read_text(const char *path, char *buf, size_t size) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    MUST(fd >= 0, "status-open");
    ssize_t got = 0, n;
    while ((n = read(fd, buf + got, size - 1 - (size_t)got)) > 0) {
        got += n;
        if ((size_t)got >= size - 1) break;
    }
    MUST(n >= 0, "status-read");
    buf[got] = '\0';
    MUST(close(fd) == 0, "status-close");
}

static unsigned long long status_hex(const char *field) {
    char buf[8192], key[32];
    read_text(TA_STATUS_PATH, buf, sizeof(buf));
    snprintf(key, sizeof(key), "\n%s:\t", field);
    const char *at = strstr(buf, key);
    MUST(at != NULL, "status-field");
    return strtoull(at + strlen(key), NULL, 16);
}

/* EXACT whole-line match. A bare strstr() is a PREFIX test and silently
 * accepts a longer field: "Uid:\t1001\t1001\t1001\t1001" is a substring of
 * "Uid:\t1001\t1001\t1001\t10010", so an fsuid of 10010 — a different user —
 * satisfied the old predicate. The match must therefore start at a line
 * boundary and end at one. */
static int status_line_is(const char *line) {
    char buf[8192];
    read_text(TA_STATUS_PATH, buf, sizeof(buf));
    size_t len = strlen(line);
    for (const char *at = buf; (at = strstr(at, line)) != NULL; at += len) {
        if (at != buf && at[-1] != '\n') continue;
        if (at[len] == '\n' || at[len] == '\0') return 1;
    }
    return 0;
}

static void all_caps_zero(const char *why) {
    MUST(!status_hex("CapInh") && !status_hex("CapPrm") && !status_hex("CapEff") &&
         !status_hex("CapBnd") && !status_hex("CapAmb"), why);
}

/* Every id in all four positions is exactly 1001, and NNP is on. */
static void identity_retired(const char *why) {
    uid_t ruid, euid, suid;
    gid_t rgid, egid, sgid;
    MUST(getresuid(&ruid, &euid, &suid) == 0 &&
         ruid == TA_UID && euid == TA_UID && suid == TA_UID, why);
    MUST(getresgid(&rgid, &egid, &sgid) == 0 &&
         rgid == TA_GID && egid == TA_GID && sgid == TA_GID, why);
    MUST(status_line_is("Uid:\t1001\t1001\t1001\t1001"), "fs-uid-readback");
    MUST(status_line_is("Gid:\t1001\t1001\t1001\t1001"), "fs-gid-readback");
    MUST(prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) == 1, "nnp-readback");
}

/* Close everything above stderr so no descriptor the caller happened to hold
 * survives into the runtime target. */
static void close_extra_fds(void) {
    DIR *fds = opendir("/proc/self/fd");
    MUST(fds != NULL, "fd-inventory");
    int keep = dirfd(fds), victims[256], n = 0;
    struct dirent *entry;
    while ((entry = readdir(fds))) {
        if (entry->d_name[0] == '.') continue;
        int fd = atoi(entry->d_name);
        if (fd <= 2 || fd == keep) continue;
        MUST(n < (int)(sizeof(victims) / sizeof(victims[0])), "fd-inventory-overflow");
        victims[n++] = fd;
    }
    MUST(closedir(fds) == 0, "fd-inventory-close");
    for (int i = 0; i < n; ++i) (void)close(victims[i]);
}

/* ---- the two entry branches ---- */

static void drop_from_root(void) {
    char cap_last[32];
    struct stat info;
    /* The binary root is about to trust must itself be root-owned and not
     * group/other writable. /usr/local/libexec is outside the chowned trees. */
    MUST(lstat(SELF, &info) == 0 && info.st_uid == 0 && !(info.st_mode & 0022),
         "trusted-binary");
    MUST(status_hex("CapEff") == MASK && status_hex("CapPrm") == MASK &&
         status_hex("CapBnd") == MASK && !status_hex("CapInh") &&
         !status_hex("CapAmb"), "exact-bootstrap-caps");
    MUST(prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0, "set-nnp");
    MUST(prctl(PR_SET_KEEPCAPS, 0, 0, 0, 0) == 0, "clear-keepcaps");
    MUST(prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0) == 0, "clear-ambient");
    read_text("/proc/sys/kernel/cap_last_cap", cap_last, sizeof(cap_last));
    int last = atoi(cap_last);
    MUST(last >= CAP_SETPCAP && last < 128, "cap-bound");
    for (int cap = 0; cap <= last; ++cap)
        MUST(prctl(PR_CAPBSET_DROP, cap, 0, 0, 0) == 0, "drop-bounding");
    MUST(setgroups(0, NULL) == 0, "clear-groups");
    MUST(setresgid(TA_GID, TA_GID, TA_GID) == 0, "drop-gid");
    MUST(setresuid(TA_UID, TA_UID, TA_UID) == 0, "drop-uid");
    struct __user_cap_header_struct header;
    struct __user_cap_data_struct data[2];
    memset(&header, 0, sizeof(header));
    memset(data, 0, sizeof(data));
    header.version = _LINUX_CAPABILITY_VERSION_3;
    MUST(syscall(SYS_capset, &header, data) == 0, "clear-capsets");
    /* Readback of every retired thing, before any runtime target exists. */
    identity_retired("uid-gid-readback");
    all_caps_zero("complete-drop-readback");
    MUST(getgroups(0, NULL) == 0, "group-readback");
    MUST(setuid(0) != 0 && errno == EPERM, "regain-root-refused");
}

static void verify_legacy_rootless(void) {
    gid_t groups[64];
    /* Legacy-rootless verification, NOT a managed-bootstrap drop receipt:
     * nothing is retired here because nothing is held. The assertion is the
     * value — a bare `docker exec` asserted none of it. */
    all_caps_zero("legacy-entry-caps-not-empty");
    identity_retired("legacy-entry-identity");
    int n = getgroups((int)(sizeof(groups) / sizeof(groups[0])), groups);
    MUST(n >= 0, "legacy-entry-groups");
    for (int i = 0; i < n; ++i)
        MUST(groups[i] == TA_GID, "legacy-entry-unexpected-group");
}

/* ---- post-drop builtins ---- */

static int is_env_name(const char *s) {
    if (!s || !*s) return 0;
    if (!(isupper((unsigned char)s[0]) || s[0] == '_')) return 0;
    for (const char *p = s + 1; *p; ++p)
        if (!(isupper((unsigned char)*p) || isdigit((unsigned char)*p) || *p == '_'))
            return 0;
    return 1;
}

/* The four operational flag families `droplet.py env` has always summarised.
 * Compiled in; matched against the NAME only, never the value, and never a
 * shell pipeline over the whole environment. */
static const char *SUMMARY_PATTERNS[] = {"AUTO_SHIP", "OLLAMA", "PIN_WRITER", "GOAL_POOL"};

static int name_matches_summary(const char *entry, size_t name_len) {
    for (size_t i = 0; i < sizeof(SUMMARY_PATTERNS) / sizeof(SUMMARY_PATTERNS[0]); ++i) {
        const char *pat = SUMMARY_PATTERNS[i];
        size_t plen = strlen(pat);
        if (plen > name_len) continue;
        for (size_t off = 0; off + plen <= name_len; ++off)
            if (strncasecmp(entry + off, pat, plen) == 0) return 1;
    }
    return 0;
}

static int env_summary(void) {
    const char *hits[256];
    int n = 0;
    for (char **e = environ; *e; ++e) {
        const char *eq = strchr(*e, '=');
        if (!eq) continue;
        if (!name_matches_summary(*e, (size_t)(eq - *e))) continue;
        if (n >= (int)(sizeof(hits) / sizeof(hits[0]))) break;
        hits[n++] = *e;
    }
    for (int i = 1; i < n; ++i) {  /* insertion sort — `| sort` without a shell */
        const char *key = hits[i];
        int j = i - 1;
        while (j >= 0 && strcmp(hits[j], key) > 0) { hits[j + 1] = hits[j]; --j; }
        hits[j + 1] = key;
    }
    if (n == 0) {
        printf("(no matching env — gates run on code defaults)\n");
    } else {
        for (int i = 0; i < n; ++i) printf("%s\n", hits[i]);
    }
    return fflush(stdout) == 0 ? 0 : 1;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "TA_OP_REFUSED:no-mode\n");
        return REFUSE;
    }
    const struct mode *mode = NULL;
    for (int i = 0; i < N_MODES; ++i)
        if (strcmp(argv[1], MODES[i].name) == 0) { mode = &MODES[i]; break; }
    if (!mode) {
        fprintf(stderr, "TA_OP_REFUSED:unknown-mode\n");
        return REFUSE;
    }
    if (argc != mode->argc) {
        fprintf(stderr, "TA_OP_REFUSED:arity\n");
        return REFUSE;
    }

    uid_t uid = getuid();
    if (uid == 0) {
        drop_from_root();
    } else if (uid == TA_UID) {
        verify_legacy_rootless();
    } else {
        fprintf(stderr, "TA_OP_REFUSED:unexpected-entry-uid\n");
        return REFUSE;
    }

    /* Everything below runs at uid/gid 1001 with all five cap sets empty. */
    close_extra_fds();

    if (mode->builtin == BUILTIN_VERSION) {
        printf("%s\n", TA_OP_VERSION);
        return fflush(stdout) == 0 ? 0 : 1;
    }
    if (mode->builtin == BUILTIN_ENV_SUMMARY) return env_summary();

    char *child[16];
    int n = 0;
    for (const char *const *a = mode->argv; *a; ++a) {
        MUST(n < (int)(sizeof(child) / sizeof(child[0])) - 2, "argv-overflow");
        child[n++] = (char *)*a;
    }
    if (mode->argc == 3) {  /* the one operand any mode accepts */
        if (!is_env_name(argv[2])) {
            fprintf(stderr, "TA_OP_REFUSED:env-name\n");
            return REFUSE;
        }
        child[n++] = argv[2];
    }
    child[n] = NULL;
    execv(child[0], child);
    fprintf(stderr, "TA_OP_REFUSED:exec errno=%d\n", errno);
    return REFUSE;
}
