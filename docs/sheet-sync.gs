/**
 * Job Radar -> Google Sheet. When you mark a job "applied" in Job Radar,
 * this adds it to your tracker tab (or updates its row if the link is
 * already there).
 *
 * Setup (once): in the sheet, Extensions > Apps Script, paste this whole file
 * over the code there, set TOKEN to the JOBRADAR_SHEET_TOKEN in .env, Save,
 * then Deploy > New deployment > Web app, Execute as: Me, Who has access:
 * Anyone > Deploy, allow access, and copy the Web app URL into .env as
 * JOBRADAR_SHEET_URL.
 * After changing this code: Deploy > Manage deployments > pencil >
 * Version: New version > Deploy (the URL stays the same).
 *
 * Columns (row 1 is the header): A Company Name, B Job role, C Current Status,
 * D Job Link, E Applied Date, F Remark.
 */
const TOKEN = 'PUT-THE-TOKEN-FROM-.env-HERE';
const TAB_ID = 2043997289;                  // only this tab: the #gid= in the sheet's link
const APPLIED = 'B Application Submitted';  // the status text the tab already uses
const TIME_ZONE = 'Asia/Kolkata';           // "today" when Job Radar sends no date

function doPost(e) {
  try {
    return reply(save(JSON.parse(e.postData.contents)));
  } catch (err) {   // answer with the reason, not Google's HTML error page
    return reply({ ok: false, error: String(err && err.message || err) });
  }
}

function save(job) {
  if (job.token !== TOKEN) return { ok: false, error: 'wrong token' };
  const sheet = SpreadsheetApp.getActive().getSheets().find((s) => s.getSheetId() === TAB_ID);
  if (!sheet) return { ok: false, error: 'tab ' + TAB_ID + ' not found' };
  if (job.ping) return { ok: true, tab: sheet.getName(), rows: sheet.getLastRow() };

  // "2026-11-16": typed into the cell like this, Sheets reads it as that date
  // whatever the sheet's locale, with no time-zone shift.
  const day = /^\d{4}-\d{2}-\d{2}$/.test(job.date || '') ? job.date
    : Utilities.formatDate(new Date(), TIME_ZONE, 'yyyy-MM-dd');

  const lock = LockService.getScriptLock();   // two saves at once must not take the same row
  lock.waitLock(20000);
  try {
    const last = sheet.getLastRow();
    const rows = last > 1 ? sheet.getRange(2, 1, last - 1, 6).getDisplayValues() : [];
    const link = norm(job.url);
    const role = job.role || job.title;           // the title without " ID: ..."
    const name = norm(job.company) + '|' + norm(role);
    // The same posting under another link ("/en-US/jobs/job/X_R0138600" vs
    // "/jobs/job/Bangalore/X_R0138600"): its job ID is in both.
    const ref = String(job.ref || '').toLowerCase();
    for (let i = 0; i < rows.length; i++) {
      const theirs = norm(rows[i][3]);
      const same = link ? theirs === link || (ref.length >= 5 && theirs.includes(ref))
                        : norm(rows[i][0]) + '|' + norm(rows[i][1]) === name;
      if (!same) continue;
      // Already listed (a referral, a draft, typed by hand...). What you typed
      // stays: only the status moves to applied, a blank remark gets Job
      // Radar's, and a role that is just the title gets the job ID added.
      const r = i + 2;
      const done = [];
      if (rows[i][2].trim() !== APPLIED) {
        sheet.getRange(r, 3).setValue(APPLIED);
        sheet.getRange(r, 5).setValue(day);
        done.push('status');
      }
      if (!rows[i][5].trim() && job.notes) {
        sheet.getRange(r, 6).setValue(job.notes);
        done.push('remark');
      }
      if (job.title !== role && norm(rows[i][1]) === norm(role)) {
        sheet.getRange(r, 2).setValue(job.title);
        done.push('role');
      }
      if (!done.length) return { ok: true, action: 'already', row: r };
      if (done.includes('status')) finish(sheet.getRange(r, 5));
      else SpreadsheetApp.flush();
      return { ok: true, action: 'updated', row: r, changed: done };
    }

    // The tab is a Sheets Table: a row written just below the last one is
    // inside it, so it gets the table's look and status dropdown by itself.
    const r = last + 1;
    sheet.getRange(r, 1, 1, 6).setValues([[job.company || '', job.title || '', APPLIED,
                                           job.url || '', day, job.notes || '']]);
    finish(sheet.getRange(r, 5));
    return { ok: true, action: 'added', row: r };
  } finally {
    lock.releaseLock();
  }
}

// Writes are applied in a batch after the script ends, where an error can't
// be caught; flushing here makes them happen now. A table's date column may
// refuse a number format - the row is written by then, so that is fine.
function finish(dateCell) {
  SpreadsheetApp.flush();
  try {
    dateCell.setNumberFormat('dd/mm/yyyy');
    SpreadsheetApp.flush();
  } catch (err) { /* the column keeps its own date format */ }
}

function norm(v) {
  return String(v || '').trim().toLowerCase().replace(/\/+$/, '');
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
