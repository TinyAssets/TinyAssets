  function restoreConfirmation(time) {
    return "Restore the whole box to " + time + "? Later files, chat/history, skills, packages and schedules will rewind. Your current state is saved as a restore point first, available until normal retention removes it. External sends, purchases and publications cannot be undone.";
  }
  function restoreTime(point) {
    const at = Number(point && point.published_at);
    if (!Number.isFinite(at) || at <= 0) return "that restore point";
    try { return new Date(at * 1000).toLocaleString(); }
    catch (_error) { return "that restore point"; }
  }
  async function ownerVolume(path, body) {
    const response = await fetch(path, {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: Object.assign({"Content-Type": "application/json"}, authHeaders()),
      body: JSON.stringify(body || {})
    });
    const document = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(document.reason || document.error || "restore_unavailable");
    return document;
  }
  async function paintRestorePoints(status) {
    const list = $("restore-points");
    if (!list) return;
    list.replaceChildren();
    let catalog;
    try { catalog = await ownerVolume("/app/owner/restore-points", {}); }
    catch (error) {
      if (status) status.textContent = "Restore points could not be read. " + error.message;
      return;
    }
    for (const point of catalog.snapshots || []) {
      const time = restoreTime(point);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn";
      button.textContent = "Undo to " + time;
      button.addEventListener("click", async () => {
        const copy = restoreConfirmation(time);
        if (!window.confirm(copy)) return;
        try {
          const result = await ownerVolume("/app/owner/restore", {
            snapshot_id: point.snapshot_id,
            expected_generation: catalog.volume_generation
          });
          if (status) status.textContent = result.pending ? "Restore is still pending." : "Restore started.";
        } catch (error) {
          if (status) status.textContent = "Restore did not start. " + error.message;
        }
      });
      list.appendChild(button);
    }
    if (!(catalog.snapshots || []).length && status) status.textContent = "No retained restore points yet.";
  }
  const savePoint = $("btn-save-restore-point");
  const restoreStatus = $("restore-status");
  if (savePoint) {
    savePoint.addEventListener("click", async () => {
      savePoint.disabled = true;
      try {
        const result = await ownerVolume("/app/owner/checkpoint", {});
        if (restoreStatus) {
          restoreStatus.textContent = result.pending
            ? "That restore point is not saved yet. It will retry; it is not usable."
            : "Restore point saved.";
        }
        await paintRestorePoints(restoreStatus);
      } catch (error) {
        if (restoreStatus) restoreStatus.textContent = "Could not save a restore point. " + error.message;
      } finally {
        savePoint.disabled = false;
      }
    });
    paintRestorePoints(restoreStatus);
  }
  window.tinyassetsRestoreConfirmation = restoreConfirmation;
