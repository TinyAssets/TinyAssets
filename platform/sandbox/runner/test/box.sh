#!/bin/sh
# One test sandbox's root (`harness.ts`, `Box`): run as root under
#   unshare --mount --pid --fork --uts --propagation private -- /bin/sh box.sh \
#     <root> <ready> <loader> <loader at> <node> <node at> <ca at>
# it binds the host's system directories into <root> read-only, gives the box a /usr/local,
# a /dev and a /proc of its own (the template's Node, loader and CA at the layout's paths,
# `layout.ts`), then pivots into <root>, so the runner runs at the sandbox's real paths and
# no host path it can see is writable. Its host pid is written to <ready> once the box is
# ready; it then stays up
# as the box's init (pid 1: it reaps every orphan, as the sandbox's own init does), until
# the harness kills it, which ends every process in the box.
set -eu
ROOT=$1
READY=$2
LOADER=$3
LOADER_AT=$4
NODE=$5
NODE_AT=$6
CA_AT=$7
exec 9>"$READY"
# This shell's pid as the host's /proc names it (its own /proc is not mounted yet).
read -r HOST_PID _ < /proc/self/stat
for dir in bin lib lib32 lib64 libx32 sbin usr; do
  if [ -L "/$dir" ]; then
    ln -s "$(readlink "/$dir")" "$ROOT/$dir"
  elif [ -d "/$dir" ]; then
    mkdir -p "$ROOT/$dir"
    mount --bind "/$dir" "$ROOT/$dir"
    mount -o remount,bind,ro "$ROOT/$dir"
  fi
done
# The template's /usr/local: Node v22 and the loader, root's alone.
mount -t tmpfs -o mode=0755,size=16m tmpfs "$ROOT/usr/local"
mkdir -p "$ROOT$(dirname "$NODE_AT")" "$ROOT$(dirname "$LOADER_AT")" "$ROOT$(dirname "$CA_AT")"
: > "$ROOT$NODE_AT"
mount --bind "$NODE" "$ROOT$NODE_AT"
mount -o remount,bind,ro "$ROOT$NODE_AT"
cp "$LOADER" "$ROOT$LOADER_AT"
chmod 0700 "$ROOT$LOADER_AT"
# The loader trusts E2B's egress CA beside the system's; the box's is the system's bundle.
if [ -f /etc/ssl/certs/ca-certificates.crt ]; then
  cp /etc/ssl/certs/ca-certificates.crt "$ROOT$CA_AT"
fi
# A minimal /dev of its own: its nodes, a terminal instance, a private /dev/shm.
mkdir -p "$ROOT/dev"
mount -t tmpfs -o mode=0755,nosuid,size=1m tmpfs "$ROOT/dev"
for node in "null 1 3" "zero 1 5" "full 1 7" "random 1 8" "urandom 1 9" "tty 5 0"; do
  set -- $node
  mknod -m 0666 "$ROOT/dev/$1" c "$2" "$3"
done
mkdir "$ROOT/dev/pts" "$ROOT/dev/shm"
mount -t devpts -o newinstance,ptmxmode=0666,mode=0620 devpts "$ROOT/dev/pts"
mount -t tmpfs -o mode=1777,nosuid,nodev,size=16m tmpfs "$ROOT/dev/shm"
ln -s pts/ptmx "$ROOT/dev/ptmx"
ln -s /proc/self/fd "$ROOT/dev/fd"
ln -s /proc/self/fd/0 "$ROOT/dev/stdin"
ln -s /proc/self/fd/1 "$ROOT/dev/stdout"
ln -s /proc/self/fd/2 "$ROOT/dev/stderr"
# This pid namespace's own /proc, then the pivot: the host's tree is gone from the box.
mkdir -p "$ROOT/proc" "$ROOT/.oldroot"
mount -t proc -o nosuid,nodev,noexec proc "$ROOT/proc"
# Its kernel knobs are the host's: read-only in the box.
for knob in sys sysrq-trigger; do
  if [ -e "$ROOT/proc/$knob" ]; then
    mount --bind "$ROOT/proc/$knob" "$ROOT/proc/$knob"
    mount -o remount,bind,ro "$ROOT/proc/$knob"
  fi
done
cd "$ROOT"
pivot_root . .oldroot
umount -l /.oldroot
rmdir /.oldroot
cd /
echo "$HOST_PID" >&9
exec 9>&-
# It also ends, and so its box, when the harness's end of its stdin closes (the harness died).
exec perl -e 'my $in = ""; vec($in, 0, 1) = 1;
  while (1) { 1 while waitpid(-1, 1) > 0;
              if (select(my $r = $in, undef, undef, 0.05) > 0) { exit 0 if !sysread(STDIN, my $b, 64) } }'
