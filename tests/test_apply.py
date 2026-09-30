"""The AI apply engine's decisions that need no browser: the queue and its
statuses, what gets typed into a question, which choice matches an answer, and
which resume goes with a job."""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jobradar.store import Store
from jobradar.apply.answers import Profile, rule_answer, choose_option, nearest_option
from jobradar.apply import resumes

# --- the queue -------------------------------------------------------------------
s = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
job = lambda i: {"id": i, "company": "Acme", "title": f"SDE {i}", "url": f"https://x/{i}",
                 "location": "Pune, India", "source": "greenhouse", "posted_at": None,
                 "description": "Java Spring Boot"}
s.upsert([job("a"), job("b"), job("c")])

a = s.enqueue("a")
b = s.enqueue("b")
row = s.application_for("a")
assert row["status"] == "queued" and row["applied_at"] is None, "queued is not applied yet"
assert row["queued_at"] is not None
assert s.enqueue("a") == a, "queueing twice keeps one row"
assert s.next_queued()["id"] == a, "oldest first"
assert "a" not in [r["id"] for r in s.unscored(9)], "a queued job costs no tokens"

s.update_application(a, "applying")
assert [r["id"] for r in s.apply_queue()] == [a, b], "the job in progress leads the queue"
assert s.next_queued()["id"] == b
s.update_application(a, "needs_you")
assert s.requeue_stuck() == 1 and s.application_for("a")["status"] == "queued"

s.finish_ai_application(a, resume="Java.pdf", proof="private/apply-proof/1.png", notes="done")
row = s.application_for("a")
assert row["status"] == "ai_applied" and row["applied_by"] == "ai_engine", "AI apply done"
assert row["applied_at"] is not None and row["resume"] == "Java.pdf"
assert [e["status"] for e in s.events(a)] == ["queued", "applying", "needs_you", "queued", "ai_applied"]
try:
    s.enqueue("a"); raise AssertionError("an applied job must not be queued again")
except ValueError:
    pass
assert [r["id"] for r in s.engine_done()] == [a]
s.update_application(b, "failed", notes="AI engine: no form")
assert s.enqueue("b") == b, "a failed job can be queued again"
assert s.application_for("b")["status"] == "queued"

# "In apply queue" picked from any status dropdown joins the queue too
c = s.set_job_status("c", "in_process")
s.update_application(c, "queued")
assert s.application_for("c")["queued_at"] is not None
assert [r["id"] for r in s.apply_queue()][-1] == c, "to the back of the line"

# --- answers ---------------------------------------------------------------------
root = Path(tempfile.mkdtemp())
(root / "private").mkdir()
(root / "private" / "apply-profile.yaml").write_text("""
personal: {first_name: Asha, last_name: Rao, email: a@x.in, phone: "9000000000",
           current_city: Pune, current_state: Maharashtra, country: India, nationality: Indian,
           gender: Female}
links: {linkedin: https://linkedin.com/in/asha}
current_job: {company: Acme, title: SDE, total_experience_years: 2, notice_period_days: 30,
              current_ctc_lpa: 8, expected_ctc_lpa: 12, currently_serving_notice: "no"}
education: {degree: B.Tech, degree_full: Bachelor of Technology, college: IIT, graduation_year: 2023}
preferences: {need_visa_sponsorship: "no", authorized_to_work_in_india: "yes", willing_to_relocate: "yes"}
diversity: {disability: "no"}
standard_answers: {how_did_you_hear: Company careers page}
""", encoding="utf-8")
os.environ["JOBRADAR_APPLY_PASSWORD"] = "test"
p = Profile(root)
ask = lambda label, kind="text": rule_answer(p, label, kind)
assert ask("First Name*") == "Asha" and ask("Surname") == "Rao"
assert ask("Full name ✱") == "Asha Rao"
assert ask("Confirm your email") == p.login_email
assert ask("Phone") == "9000000000"
assert ask("LinkedIn Profile") == "https://linkedin.com/in/asha"
assert ask("Expected CTC (in LPA)", "number") == "12"
assert ask("Expected CTC", "number") == "1200000", "a rupee box gets rupees"
assert ask("Current CTC") == "8 LPA"
assert ask("Notice period", "choice") == "30"
assert ask("Do you need a work visa?", "choice") == "No"
assert ask("Will you now or in the future require sponsorship?", "choice") == "No"
assert ask("State") == "Maharashtra"
assert ask("By checking this box, you consent to the processing of your data per your stated "
           "preferences and our privacy policy", "choice") == "Yes", "consent, not 'State'"
assert ask("How many years of experience do you have with Kafka?") is None, "no guessing"
assert ask("Why do you want to join us?") is None, "prose is written, not ruled"
p.learn("Kafka years?", "1")
assert Profile(root).recall("Kafka years? *") == "1", "your answers are kept and reused"

# --- matching an answer to the choices ------------------------------------------------
assert choose_option("30", ["Immediate", "15 days or less", "16-30 days", "60+ days"]) == "16-30 days"
assert choose_option("2", ["0-1 years", "1-3 years", "3-5 years"]) == "1-3 years"
assert choose_option("Female", ["Man", "Woman", "Non-binary"]) == "Woman"
assert choose_option("Decline to self-identify", ["Asian", "I don't wish to answer"]) == "I don't wish to answer"
assert choose_option("Yes", ["I confirm"]) == "I confirm", "consent lists say yes their own way"
assert choose_option("No", ["Yes", "No"]) == "No"
assert choose_option("India", ["India +91", "British Indian Ocean Territory +246"]) == "India +91"
assert choose_option("Company careers page || LinkedIn", ["Referral", "LinkedIn"]) == "LinkedIn"
assert choose_option("Kotlin", ["Java", "Python"]) is None
assert nearest_option("Bengaluru", ["Bangalore", "Mumbai"]) == "Bangalore", "nearest spelling"
assert nearest_option("Yes", ["Yes, full-time", "No"]) == "Yes, full-time"
assert nearest_option("No", ["Nope, never", "Sometimes"]) is None, "yes/no never guessed by spelling"

# --- resume choice ---------------------------------------------------------------------
cands = [{"file": "java.pdf", "keywords": ["java", "spring boot"]},
         {"file": "mern.pdf", "keywords": ["react", "node"]},
         {"file": "sde.pdf", "keywords": ["software engineer"], "default": True}]
for c in cands:
    (root / c["file"]).write_bytes(b"")
resumes.extract_text = lambda path: ""
pick = lambda title, desc="": Path(resumes.pick(cands, root, title, desc)["file"]).stem
assert pick("Java Backend Developer", "Spring Boot microservices") == "java"
assert pick("Frontend Engineer", "React, Node and TypeScript") == "mern"
assert pick("Software Engineer", "") == "sde", "no lean: the general resume"
assert pick("Software Engineer", "java or react") == "sde", "a near tie: the general resume"

print("apply engine tests PASS")
