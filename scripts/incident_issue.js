'use strict';

// One open incident issue per label. A failure comments on the newest open
// match, or opens one when none exists. Recovery comments and closes that
// same issue. Deploy prod and the uptime canary both call this; neither
// workflow inlines the list/comment/create/close sequence.
//
// "Newest" is the first match returned by listForRepo with sort=created and
// direction=desc, scanning later pages until a match or a short page. Issues
// whose titles start with an excluded prefix (owner-content-repair shares the
// deploy-failed label) are skipped. Pull requests are skipped. This updates
// one issue; it does not close the rest of a backlog.

const PER_PAGE = 100;
const PAGE_CAP = 10;

function repoOf(context) {
  const repo = context && context.repo;
  if (!repo || !repo.owner || !repo.repo) {
    throw new Error('incident_issue: context.repo owner and repo are required');
  }
  return {owner: repo.owner, repo: repo.repo};
}

function requireLabel(label) {
  if (typeof label !== 'string' || !label.trim()) {
    throw new Error('incident_issue: label is required');
  }
}

function requireBody(body, purpose) {
  if (typeof body !== 'string' || !body.trim()) {
    throw new Error(`incident_issue: body is required to ${purpose}`);
  }
}

function excluded(title, prefixes) {
  if (!prefixes || !prefixes.length) return false;
  const text = String(title || '');
  return prefixes.some((prefix) => text.startsWith(prefix));
}

function matches(issue, excludeTitlePrefixes) {
  if (!issue || issue.pull_request) return false;
  return !excluded(issue.title, excludeTitlePrefixes);
}

async function findOpenIncident(github, context, options) {
  requireLabel(options.label);
  const base = repoOf(context);
  const perPage = options.perPage || PER_PAGE;
  const pageCap = options.pageCap || PAGE_CAP;
  if (!Number.isInteger(perPage) || perPage < 1 || perPage > PER_PAGE) {
    throw new Error('incident_issue: perPage must be an integer from 1 to 100');
  }
  if (!Number.isInteger(pageCap) || pageCap < 1) {
    throw new Error('incident_issue: pageCap must be a positive integer');
  }
  for (let page = 1; page <= pageCap; page += 1) {
    const response = await github.rest.issues.listForRepo({
      ...base,
      state: 'open',
      labels: options.label,
      per_page: perPage,
      page,
      sort: 'created',
      direction: 'desc',
    });
    const batch = response && response.data;
    if (!Array.isArray(batch)) {
      throw new Error('incident_issue: listForRepo returned no issue list');
    }
    for (const issue of batch) {
      if (matches(issue, options.excludeTitlePrefixes)) return issue;
    }
    if (batch.length < perPage) return null;
  }
  throw new Error(
    `incident_issue: more than ${pageCap * perPage} open '${options.label}' issues; refusing to open another`,
  );
}

async function allowCreate(options) {
  const allow = options.allowCreate;
  if (typeof allow === 'function') return await allow();
  return allow !== false;
}

async function recordFailure(github, context, options) {
  const open = await findOpenIncident(github, context, options);
  if (open) {
    const body = options.commentBody || options.body;
    requireBody(body, 'update an incident');
    await github.rest.issues.createComment({
      ...repoOf(context),
      issue_number: open.number,
      body,
    });
    return {action: 'commented', number: String(open.number)};
  }
  if (!(await allowCreate(options))) return {action: 'none', number: ''};
  const title = options.title;
  if (typeof title !== 'string' || !title.trim()) {
    throw new Error('incident_issue: title is required to open an incident');
  }
  if (excluded(title, options.excludeTitlePrefixes)) {
    throw new Error('incident_issue: new title is excluded from later lookup');
  }
  const body = options.createBody || options.body;
  requireBody(body, 'open an incident');
  const created = await github.rest.issues.create({
    ...repoOf(context),
    title,
    labels: [options.label],
    body,
  });
  const number = created && created.data && created.data.number;
  if (!number) throw new Error('incident_issue: create did not return an issue number');
  return {action: 'opened', number: String(number)};
}

async function recordRecovery(github, context, options) {
  const open = await findOpenIncident(github, context, options);
  if (!open) return {action: 'none', number: ''};
  const body = options.body;
  requireBody(body, 'close an incident');
  const base = repoOf(context);
  await github.rest.issues.createComment({
    ...base,
    issue_number: open.number,
    body,
  });
  await github.rest.issues.update({
    ...base,
    issue_number: open.number,
    state: 'closed',
    state_reason: 'completed',
  });
  return {action: 'closed', number: String(open.number)};
}

module.exports = {
  findOpenIncident,
  recordFailure,
  recordRecovery,
};
