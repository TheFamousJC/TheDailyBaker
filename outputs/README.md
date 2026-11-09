# The Daily Baker

A local RSS-to-satire pipeline with an approval queue. Install Python 3.11 or newer and run these commands from the project folder in PowerShell.

```powershell
python -m pip install -r outputs/requirements.txt
python outputs/daily_baker.py generate --demo
Invoke-Item work/daily-baker/review.html
python outputs/daily_baker.py generate
```

Live generation checks configured RSS feeds for items published in the last 48 hours. It looks for evidence of opposite positions from distinct configured publisher groups and ranks matching topics by publisher coverage. It skips a run if no pair qualifies. The initial topic rules and three pairs of prewritten jokes per topic live in `settings.json`. This approach is deliberately narrow. Review each link for context, attribution and syndication before approval.

Copy a draft ID from the review page. Run one of the following commands to record a decision, then refresh the page.

```powershell
python outputs/daily_baker.py approve DRAFT_ID
python outputs/daily_baker.py reject DRAFT_ID
python outputs/daily_baker.py export DRAFT_ID
```

Export creates a ZIP in `outputs` with the PNG, a copy-ready caption.txt with article citations, and a JSON record containing the caption, alt text, and source links. New images name both outlets in the footer. Export requires an approved live draft. Synthetic demo drafts can be reviewed and stay local.

For daily collection, create a daily Windows Task Scheduler task. Use the full path to the Python executable that has Pillow installed as the program and pass the quoted full path to `outputs\daily_baker.py` followed by `generate` as its arguments. Set the trigger to the local time you want. The task refreshes the review queue in `work\daily-baker`. It requires network access to your chosen RSS feeds.


The source roster uses the requested channels, excluding Al Jazeera. Entries without a usable direct RSS feed use a domain-scoped Google News RSS query. This follows indexed web coverage, which can differ from the individual TV broadcast. Related channels share a publisher group. Headlines obtained through Google News link through Google News and are matched on headline text alone.
