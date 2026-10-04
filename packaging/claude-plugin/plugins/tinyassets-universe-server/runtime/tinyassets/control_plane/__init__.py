"""The control plane's always-on duties: owner lease seam, trigger table, wake path.

Target architecture D7 (``openspec/changes/target-architecture``): the control
plane is the only always-on layer, and boxes and jails keep no timers.

* ``lease`` -- the execution owner's lease as duties consume it (S8a installs
  the real one).
* ``cadence`` -- the engagement-decayed proactive cadence policy.
* ``triggers`` -- the trigger table and fire ledger (platform state).
* ``scheduler`` -- the owner tick that fires owed triggers, and its metrics.
* ``wake`` -- what a fire calls: the run path, later ``BoxProvider.ensure_awake``.

Every periodic loop in the platform is classified in
``tests/control_plane_timer_inventory.py``.
"""
