# Start here: Shehab's internship setup

Two parts. **Part 1** (15 minutes, no code) gets you most of the value: job alerts, Simplify, a tracker, and people. **Part 2** is the pipeline and its Control Center: one page that finds postings, scores them, builds your resumes and cover letters, and keeps the Google Sheet in sync. If the pipeline ever breaks during midterms, ignore it. Part 1 still works.

Stuck? Paste the error into the Claude project and say `/pipeline`.

---

# Part 1: the 15-minute version

## 1. Job alerts sent to your Gmail (7 min)
Make saved searches with daily email alerts on each of these. These are the boards Houston employers post to, and Bauer says Gateway postings are verified real with smaller applicant pools.
- **Bauer Career Gateway** (sign in through AccessUH): Jobs, Search, Finance OR Accounting, Internship, Houston, then **Save as Search Agent**
- **Cougar Pathway / Handshake**: saved search "finance intern" + "accounting intern", Houston, alerts on
- **LinkedIn Jobs**: "finance intern Houston", "accounting intern Houston", "treasury intern", "credit analyst intern", daily alerts
- **Indeed**: same searches, email alerts
Send every alert to **shehabby05@gmail.com**. In Gmail, make one filter that labels them **Internship Alerts** and skips the inbox.

## 2. Simplify Copilot (5 min)
Install the free **Simplify Copilot** Chrome extension (skip the paid Simplify+). Fill its profile from `master_profile.json` and upload your 3 track resumes (Make track resumes button, Part 2). It autofills Workday, Greenhouse and Lever forms; **you review and click Submit.** For Workday each company needs its own account: let Chrome's password manager suggest and save the password.

## 3. Google Sheet tracker
"Shehab Internship Tracker" in Google Sheets. The pipeline adds a **Pipeline Inbox** tab and an **Outreach** tab and never touches your other tabs. Column R, **My Notes**, is yours and is never written by the pipeline.

## 4. Found a good posting in an alert?
Add a row to **Pipeline Inbox** with Company, Role and Link (paste the posting text into Description if you can). The next scan scores it and builds a packet if it qualifies. Type "career fair" or "campus" in Notes to route it to your school email.

## 5. The Claude project, 1 to 3 times a week
`/cover-letter` for a top role, `/network` for an alumni message, `/interview` for prep, `/status` to see where things stand.

## Your week (about 4.5 to 5 hours, Nov to Feb)
| What | Time |
|---|---|
| Skim the Internship Alerts label + Sheet | 30 min |
| 5 to 8 targeted applications (Simplify + track resume) | 1.5 hr |
| 5 UH-alumni LinkedIn messages + 1 to 2 short calls | 1.5 hr |
| One org meeting or employer event | 1 hr |
| Follow-ups + tracker | 20 min |

About 40% applications, 60% people. Referrals beat volume in 2026.

## Dates
- **Bauer Career Launch:** Jan 14, 2027, 2 to 4 pm, Melcher Hall
- **Bauer Business Career Fair:** Feb 12, 2027, 11 am to 3 pm, UH Hilton
- **Beta Alpha Psi:** last spring's deadline was Feb 23; expect similar
- **Bauer Finance Association:** $45/semester, joins at the start of each semester
- **Open now, verify each is still live:** Albemarle/Ketjen 2027 Finance Summer Intern (Gateway, expires Jan 12, 2027); Grant Thornton Summer 2027 intern (Gateway)

---

# Part 2: the pipeline and Control Center

## What is where
- `Start Control Center` on your desktop: opens the page (http://127.0.0.1:8765, this computer only). Quit from the page.
- `tools\`: the other .bat files (apply, network, check watchlist, schedule, tests). The Control Center buttons run the same scripts, so you rarely need these.
- `INSTALL.bat`: stays in the main folder. Safe to run again any time; it also puts the desktop shortcut back.
- `config.yaml`: all switches. `master_profile.json`: the only source of facts for resumes.

## Setup (once)
1. **Python.** You have Python 3.13, which works. If you reinstall: python.org/downloads, tick "Add python.exe to PATH".
2. **INSTALL.bat.** Double-click it in `Downloads\Work\InternshipPipeline`. It builds the private Python environment, installs everything (including the Control Center), runs a SETUP CHECK, and puts **Start Control Center** on the desktop.
3. **Free Gemini key.** aistudio.google.com/apikey (shehabby05 account), Create API key. Paste it after `GOOGLE_API_KEY=` in `.env` (Notepad). The free tier may use prompts for training, so the pipeline only sends job postings, never your information.
4. **Google Sheet.** Upload `Shehab_Application_Tracker.xlsx` to Drive, Save as Google Sheets, name it exactly **Shehab Internship Tracker**.
5. **Let the pipeline write to the Sheet.** console.cloud.google.com: new project `internship-pipeline`, enable **Google Sheets API** and **Google Drive API**, create a service account `pipeline`, add a JSON key, rename it `google_service_account.json` and put it in the pipeline folder. Share the Sheet with the key's `client_email` as Editor (uncheck Notify).
6. **Claude Code (optional, uses your Pro plan).** PowerShell: `irm https://claude.ai/install.ps1 | iex`, reopen, run `claude`, choose Claude account with subscription. Never set an `ANTHROPIC_API_KEY` (that switches to paid API billing). Without it, tailoring uses the built-in method.
7. **Schedule it.** Right-click `tools\SCHEDULE_AT_LOGIN.bat` > Run as administrator. One scan a day: 6:40 AM (wakes the laptop if plugged in and wake timers are on), or your first sign-in/unlock after that. It runs hidden at low priority, so no window pops up; results and any problem show in the Control Center (Today, Health). Change the rhythm with `scan_every_hours` in `config.yaml` (keep it at 20 or more).

## Using the Control Center
- **Run Daily Scan:** finds new postings, scores them, builds packets. Never submits.
- **Counters:** New today, To review (qualified or blocked jobs), Ready to apply (packets and opened-but-not-submitted), Follow-ups due (applied 8 to 21 days ago).
- **Tools:** Test setup, Sync Sheet now, Make track resumes, Rebuild packets (after a profile or tailoring change), Simplify setup (one time), Network draft (LinkedIn note, email, follow-up and thank-you drafts; nothing is sent), Status summary, Code self-test.
- **One job at a time.** If the 6:40 AM run is going, the buttons wait. Stop ends the current job; work already saved stays.
- **Keys are never shown**, only "present" or "missing".
- If the page won't open: look at `control_center.log` in the pipeline folder, or run INSTALL.bat again.

## The Today and Network tabs
- **Today** (opens first): new matches from the last 3 days (Review opens the documents), how many are ready to apply, follow-ups due (applications 7 to 21 days old with a Draft follow-up button, plus networking contacts whose follow-up date has arrived), upcoming dates (edit `events:` in `config.yaml`), and quick links (Gateway via AccessUH, Simplify, the Claude project, UH alumni on LinkedIn, and the Google Sheet once Network has been refreshed).
- **Network:** everyone you have drafted a message for, with last touch, status and next touch. Refresh from Sheet pulls the Outreach tab. Pick a row, then I sent it (Status Sent, next touch in 7 days), Show follow-up text, Open drafts folder, or Find UH alumni. Draft message makes a new kit. Nothing is ever sent for you.
- **Cold outreach (Network tab, Prospects):** companies ranked by fit with your background (your three tracks plus healthcare finance, which matches your CVS claims and billing work; companies with an open matching job in the pipeline rank higher). Pick one, use Find UH alumni there or Find recruiting contact to get a real person, then Draft cold email. The draft asks about their summer 2027 internships and for 10 minutes, built only from your verified profile. Under Your outreach, pick it and press **Open in Outlook** (school email) or **Open in Gmail**: the message opens unsent, you read it and click Send yourself, then press **I sent it**. Later: **I followed up**, **They replied** (stops reminders). Follow-ups come due 7 days after each send and show on Today. Edit the company list in `prospects.yaml`. Aim for about 5 a week.
- **Health** (Scan & tools tab): last scan, last Sheet sync, the 6:40 AM scheduled task's status, Simplify in the apply window, submit mode, and the last automatic run's output.
- If a job fails, the log says what to do and the page shows a red note. Nothing is submitted or sent when that happens.
- The 6:40 AM run and every button's job run at below-normal priority, so games come first.

## Using the Jobs, Documents and Apply tabs
- **Jobs:** every job with its score and status. Pick a row, then Open posting, Preview documents, Build packet, Skip, or I submitted it. Changes go to the Google Sheet in the same step.
- **Documents:** the resume and cover letter side by side (the PDF, or a Word preview if no PDF yet), the fact-check note, the posting text, and Open folder.
- **Apply (about 20 minutes, whenever you choose):**
  1. Pick the application (best first). Open application opens just that one in the apply window and fills what it safely can; the fill sheet below has every answer ready to paste.
  2. Sign in or create the account there (Chrome's password suggester; the email verification link and any CAPTCHA are yours). Finish custom questions (`/answer` in the project), reach the Review page, and **click Submit yourself**.
  3. Close that Chrome window, then press **I submitted it** (Applied, today's date, follow-up in 7 days, outreach drafts written, nothing sent), **Not yet** (stays in the queue) or **Skip this job**.
  - Prefer Simplify? **Use Simplify instead** opens the posting in your normal Chrome; press I submitted it afterwards.
  - Nothing on the page clicks Submit or sends anything, even if `submit_mode` is set to approve.
- **Re-check old jobs** (Scan & tools): runs today's filters over jobs you have not opened or applied to and marks the misfits Skipped, with the reason in Notes. Filled and Applied jobs are never touched. Packet folders stay on disk.
- `tools\APPLY.bat` still works (several tabs at once, y/n answers in the black window).

## Emails
Applications, portal accounts, resumes and cover letters use **shehabby05@gmail.com**. Jobs whose Company, Source or Notes mention UH, Bauer, Gateway, Handshake, campus or career fair use **samoqbel@cougarnet.uh.edu**. Track resumes: `output\_track_resumes\` (Gmail) and `output\_track_resumes\Gateway\` (school email).

## Filters (changed Nov 18)
A posting is kept only if its title is an internship, its location is in the Houston area (no Remote, no other Texas cities), and its title has none of the skip words in `config.yaml` (engineering, IT, lab, supply chain, operations, scheduling, sales support, purchasing, project controls and others). Score 80 or higher qualifies. The same job listed twice under slightly different titles counts once.

## Networking (the 60%)
Control Center, **Network draft**: company plus the person's name gives you four drafts in `output\_networking\<Company>\<Person>\` and a UH-alumni search link. Each draft is logged in the Sheet's **Outreach** tab. Set Status to `Sent` when you send it and the summary reminds you when the follow-up is due.

## Optional
- **Cloud morning scan (GitHub Actions):** private repo with only the `pipeline` folder, `config.yaml`, `requirements-cloud.txt` and `watchlist_found.yaml`; workflow from `morning_scan_workflow.yml`; secrets `GOOGLE_API_KEY` and `GOOGLE_SERVICE_ACCOUNT_JSON`. Never upload `master_profile.json`, `.env`, the key file, `pipeline.db`, `output`, `browser_profile` or `.venv`. Re-upload `config.yaml` after you change it.
- **Hunter.io** free key into `.env` as `HUNTER_API_KEY=`; add company domains under `company_domains`.
- **Job boards off:** `boards_laptop: []` in `config.yaml` stops LinkedIn/Indeed scraping.
- **Watchlist:** `tools\CHECK_WATCHLIST.bat` re-tests every career-site link.

## Weekly
Update `master_profile.json` with anything new (courses, certifications, orgs), press Make track resumes and Rebuild packets, glance at `pipeline.log`.
