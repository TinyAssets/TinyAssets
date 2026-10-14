// Protected owner transfer UI. Every user-controlled string becomes a text node.
let active = null;

function element(tag, text = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}

function checkbox(label, parent) {
  const row = element("label");
  row.style.display = "block";
  const input = element("input");
  input.type = "checkbox";
  row.append(input, document.createTextNode(" " + label));
  parent.append(row);
  return input;
}

export function openTransfer({commandCenter, request, isCurrent}) {
  if (active) active.close();
  const dialog = element("dialog");
  dialog.id = "command-center-transfer";
  dialog.className = "model-dialog";
  active = dialog;
  dialog.style.maxWidth = "min(52rem, 90vw)";
  dialog.style.maxHeight = "90vh";
  const title = element("h2", "Export or import command center");
  const status = element("p");
  status.setAttribute("role", "status");
  const signIn = element("a", "Sign in to review private files");
  signIn.href = "/app/owner-sign-in";
  signIn.target = "_blank";
  signIn.rel = "noopener";
  signIn.hidden = true;
  const list = element("div");
  const review = element("div");
  const refresh = element("button", "Choose files");
  const previewButton = element("button", "Preview selected files");
  const download = element("button", "Download private zip");
  const importFile = element("input");
  importFile.type = "file";
  importFile.accept = ".zip,application/zip";
  importFile.setAttribute("aria-label", "Command center zip to import");
  const importButton = element("button", "Import into quarantine");
  const importReview = element("div");
  const reviewImport = element("button", "Review imported files");
  const copyImport = element("button", "Copy reviewed files into this box");
  reviewImport.disabled = copyImport.disabled = true;
  let importId = null, importPlan = null, binaryChoices = [];
  const close = element("button", "Close");
  for (const button of [refresh, previewButton, download, importButton, reviewImport, copyImport, close]) {
    button.type = "button";
    button.className = "btn btn--link";
  }
  previewButton.disabled = true;
  download.disabled = true;
  let choices = [], reviewed = [], previewId = null, busy = false;
  const current = () => dialog.open && isCurrent();
  function stale() {
    if (current()) return false;
    dialog.close();
    return true;
  }
  function resetReview() {
    previewId = null;
    reviewed = [];
    review.replaceChildren();
    download.disabled = true;
  }
  async function call(body, bytes = false) {
    if (stale()) throw new Error("Account changed. Open transfer again.");
    const response = await request({...body, command_center: commandCenter});
    if (stale()) throw new Error("Account changed. Open transfer again.");
    if (!response.ok) {
      const doc = await response.json();
      if (stale()) throw new Error("Account changed. Open transfer again.");
      signIn.hidden = doc.error !== "protected_owner_session_required";
      if (response.status === 409) resetReview();
      const messages = {
        protected_owner_session_required: "Sign in to the protected owner view, then try again.",
        box_provider_unavailable: "Transfer is not available for this command center yet.",
        source_changed_preview_again: "The files or rules changed. Choose and preview them again.",
        transfer_busy: "Transfer is busy. Try again shortly.",
        transfer_not_authorized: "This command center is no longer available to this account.",
      };
      throw new Error(messages[doc.error] || doc.detail || "Transfer could not finish.");
    }
    const result = await (bytes ? response.blob() : response.json());
    if (stale()) throw new Error("Account changed. Open transfer again.");
    signIn.hidden = true;
    return result;
  }
  async function work(action) {
    if (busy || stale()) return;
    busy = true;
    status.textContent = "Working…";
    try { await action(); }
    catch (error) { if (current()) status.textContent = error.message; }
    finally { busy = false; }
  }
  refresh.onclick = () => work(async () => {
    resetReview();
    choices = [];
    previewButton.disabled = true;
    list.replaceChildren();
    const result = await call({operation: "list"});
    for (const file of result.files) {
      choices.push({path: file.path, input: checkbox(file.path + " (" + file.size + " bytes)", list)});
    }
    previewButton.disabled = !choices.length;
    status.textContent = choices.length ? "Choose the files to preview. Nothing is included yet."
      : "No eligible files are available.";
  });
  previewButton.onclick = () => work(async () => {
    resetReview();
    const paths = choices.filter(item => item.input.checked).map(item => item.path);
    if (!paths.length) throw new Error("Choose at least one file to preview.");
    const result = await call({operation: "preview", paths});
    previewId = result.preview;
    review.append(element("p", result.limitations));
    for (const rule of result.rules || []) {
      const details = element("details");
      const pre = element("pre", rule.content);
      pre.style.whiteSpace = "pre-wrap";
      details.append(element("summary", rule.path + " — included with this agent"), pre);
      review.append(details);
    }
    for (const file of result.files) {
      const section = element("section");
      const input = checkbox("Include " + file.path + (file.binary ? " (binary; cannot preview)" : ""), section);
      input.disabled = Boolean(file.blocked);
      if (file.blocked) section.append(element("p", "Excluded: " + file.blocked));
      if (file.content !== null) {
        const details = element("details");
        const pre = element("pre", file.content);
        pre.style.whiteSpace = "pre-wrap";
        pre.style.overflowWrap = "anywhere";
        details.append(element("summary", "Read file"), pre);
        section.append(details);
      }
      const contacts = file.contacts.map(contact => ({index: contact.index,
        input: checkbox("Keep private contact: " + contact.value, section)}));
      reviewed.push({file, input, contacts});
      review.append(section);
    }
    download.disabled = false;
    status.textContent = "Review the content, then select what to include. Contacts are removed by default.";
  });
  download.onclick = () => work(async () => {
    if (!previewId) throw new Error("Preview the files again.");
    const selected = reviewed.filter(item => item.input.checked && !item.input.disabled);
    const includedContacts = {};
    for (const item of selected) {
      const indices = item.contacts.filter(contact => contact.input.checked).map(contact => contact.index);
      if (indices.length) includedContacts[item.file.path] = indices;
    }
    const blob = await call({operation: "export", preview: previewId, profile: "export",
      selected: selected.map(item => item.file.path),
      named_binaries: selected.filter(item => item.file.binary).map(item => item.file.path),
      included_contacts: includedContacts}, true);
    const url = URL.createObjectURL(blob);
    const link = element("a", "Download");
    link.href = url;
    link.download = "command-center.zip";
    dialog.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    status.textContent = "Private download prepared. Publishing requires a separate review.";
  });
  importButton.onclick = () => work(async () => {
    const file = importFile.files[0];
    if (!file) throw new Error("Choose a command center zip first.");
    if (file.size > 8 * 1024 * 1024) throw new Error("The zip is larger than the 8 MiB import limit.");
    const data = new Uint8Array(await file.arrayBuffer());
    if (stale()) return;
    const chunks = [];
    for (let i = 0; i < data.length; i += 32768) {
      chunks.push(String.fromCharCode(...data.subarray(i, i + 32768)));
    }
    importId = importPlan = null;
    importReview.replaceChildren();
    reviewImport.disabled = copyImport.disabled = true;
    const result = await call({operation: "import", archive: btoa(chunks.join(""))});
    importId = result.import_id;
    reviewImport.disabled = false;
    status.textContent = "Imported into quarantine. No files or schedules were activated. Reference: " + result.import_id;
    importFile.value = "";
  });
  reviewImport.onclick = () => work(async () => {
    importPlan = null;
    binaryChoices = [];
    importReview.replaceChildren();
    copyImport.disabled = true;
    const result = await call({operation: "import-preview", import_id: importId});
    importPlan = result;
    importReview.append(element("p", "Copy destination: " + result.destination +
      ". This is a separate folder. Your active agent stays as it is; no imported code runs."));
    for (const file of result.files) {
      const details = element("details");
      details.append(element("summary", file.to + " (" + file.size + " bytes)"));
      if (file.binary) {
        binaryChoices.push({path: file.path,
          input: checkbox("I reviewed this binary separately: " + file.path, details)});
      } else {
        const pre = element("pre", file.content);
        pre.style.whiteSpace = "pre-wrap";
        pre.style.overflowWrap = "anywhere";
        details.append(pre);
      }
      importReview.append(details);
    }
    copyImport.disabled = false;
    status.textContent = "Review every file before copying. Existing files are never overwritten.";
  });
  copyImport.onclick = () => work(async () => {
    if (!importPlan) throw new Error("Review the imported files first.");
    if (binaryChoices.some(item => !item.input.checked)) {
      throw new Error("Review and name each binary before copying.");
    }
    const result = await call({operation: "import-activate", request: importPlan.request,
      named_binaries: binaryChoices.map(item => item.path)});
    copyImport.disabled = true;
    status.textContent = "Files copied to " + result.destination +
      ". No imported code or schedule was started.";
  });
  close.onclick = () => dialog.close();
  dialog.addEventListener("close", () => {
    if (active === dialog) active = null;
    resetReview();
    choices = [];
    importId = importPlan = null;
    binaryChoices = [];
    importReview.replaceChildren();
    list.replaceChildren();
    dialog.remove();
  });
  dialog.append(title, element("p", "Export stays private. Import keeps code inactive until a separate activation."),
    status, signIn, refresh, list, previewButton, review, download,
    element("h3", "Import a command center"), importFile, importButton, reviewImport, importReview, copyImport, close);
  document.body.append(dialog);
  dialog.showModal();
  refresh.click();
}
