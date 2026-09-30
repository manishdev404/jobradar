"""Board discovery, offline parts: slugs guessed from a name, boards spotted in
a careers page, and the input lines find-boards accepts."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jobradar.discover import _boards_in, _domain_of, parse_lines, same_company, slug_guesses

# --- slugs from a name ------------------------------------------------------------
g = slug_guesses("Dream Sports Pvt Ltd", "dreamsports.group")
assert g[0] == "dreamsportspvtltd" and "dreamsports" in g and len(g) <= 4, g
assert slug_guesses("Razorpay", "razorpay.com") == ["razorpay", "razorpayhq"], \
    slug_guesses("Razorpay", "razorpay.com")
assert "blinkit" in slug_guesses("Zomato Blinkit", "blinkit.com")

# --- a guessed Greenhouse board must carry the company's own name ------------------
assert same_company("Razorpay", "Razorpay Software Private Limited")
assert same_company("Swiggy Instamart", "Swiggy", "swiggy.com"), "domain root counts"
assert not same_company("Lighthouse Storage", "Lighthouse")
assert not same_company("Zeta", "")

# --- boards linked or embedded in a page ------------------------------------------
page = """<a href="https://jobs.lever.co/meesho">Jobs</a>
<script src="https://boards.greenhouse.io/embed/job_board/js?for=groww"></script>
<a href="https://acme.wd3.myworkdayjobs.com/en-US/External/job/x">x</a>
<a href="https://jobs.lever.co/meesho/abc">again</a>
<a href="https://careers.example.com/open">not a board</a>"""
assert _boards_in(page) == [("greenhouse", "groww"), ("lever", "meesho"),
                            ("workday", "acme/wd3/External")], _boards_in(page)

# --- the domain behind a careers link ---------------------------------------------
assert _domain_of("https://careers.quest-global.com/global/en/job/1") == "quest-global.com"
assert _domain_of("https://www.phonepe.com/careers/") == "phonepe.com"
assert _domain_of("https://jobs.lever.co/fampay") == "", "a board host is not the company's site"

# --- input lines -------------------------------------------------------------------
items = parse_lines("# comment\n\nZepto|zeptonow.com\nZepto|dup.com\nGroww\n"
                    "Adobe|workday|adobe/wd5/external_experienced|"
                    "https://adobe.wd5.myworkdayjobs.com/external_experienced|high\n")
assert [i["name"] for i in items] == ["Zepto", "Groww", "Adobe"], items
assert items[0]["domain"] == "zeptonow.com" and items[1]["domain"] == ""
assert items[2]["hint"] == ("workday", "adobe/wd5/external_experienced")
print("discover tests PASS")
