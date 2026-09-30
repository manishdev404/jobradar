"""The model's two jobs in an application, both held to the facts:

- prose: "Tell us about yourself", "Why this role?" - written from the resume
  sent with this application and your profile, nothing else.
- infer: a short or multiple-choice question no rule covers, e.g. "Years of
  experience with Kafka?". Answered only when the resume or profile states it;
  otherwise it returns None and the engine asks you.
"""
from __future__ import annotations

import json
import re

from .answers import Profile

_RULES = """Rules you must follow:
- Use ONLY facts in the RESUME and PROFILE below. Never invent an employer, skill,
  number, date, degree, certification or project.
- First person, as the candidate. Plain text, no markdown, no placeholders."""


class Writer:
    def __init__(self, llm, profile: Profile):
        self.llm = llm            # anything with .complete(prompt, max_tokens=...)
        self.profile = profile

    def _ask(self, prompt: str, max_tokens: int) -> str:
        if self.llm is None:
            raise RuntimeError("no model configured (llm.provider in config.yaml)")
        # Reasoning models spend part of the budget thinking first.
        return (self.llm.complete(prompt, max_tokens=max_tokens + 800) or "").strip()

    def prose(self, question: str, job: dict, resume_text: str, max_words: int = 110) -> str:
        name = self.profile.full_name
        prompt = f"""You are filling in a job application for the candidate.
{_RULES}
- At most {max_words} words. Specific to this company and role: connect what the JOB
  DESCRIPTION asks for to what the RESUME shows the candidate has done.
- A message to the hiring team or a cover letter: a short greeting, 2-3 sentences
  matching resume experience to the role's needs, a closing line. Sign as {name}.

JOB: {job.get('title', '')} at {job.get('company', '')}
JOB DESCRIPTION (excerpt): {(job.get('description') or '')[:2500]}

PROFILE:
{self.profile.facts()}

RESUME:
{resume_text[:6000]}

QUESTION ON THE FORM: {question}

Answer:"""
        text = self._ask(prompt, 400)
        text = re.sub(r"^\s*(answer|response)\s*:\s*", "", text, flags=re.I)
        return text.strip().strip('"')

    def infer(self, question: str, options: list[str], job: dict, resume_text: str) -> str | None:
        """Answer a question no rule covers, the way the candidate would, from
        what the resume, profile and self-rated skills say. UNKNOWN only for
        facts nothing here states (an ID number, a date never given)."""
        choices = ("\nCHOICES (answer with one of these exactly):\n- " + "\n- ".join(options)
                   if options else "")
        ratings = self.profile.skill_ratings()
        prompt = f"""A job application form asks the candidate a question. Answer it for them.
{_RULES}
- Use judgement, as the candidate would: most questions CAN be answered from the
  RESUME, PROFILE and SKILL RATINGS below. Pick the most reasonable truthful answer.
- Rate yourself / proficiency / comfort / familiarity with a skill: a 10/10 skill is
  expert (9 of 10, "Expert", Yes); an 8/10 skill is advanced (8, "Advanced", Yes);
  a skill used at work on the resume is proficient (7); only in a project or skills
  list is intermediate (5); not anywhere is beginner (2, "No").
- Years with a skill: used at the job on the resume -> {self.profile.get('current_job.total_experience_years')}
  (the job started {self.profile.get('current_job.start_date')}); only in projects -> 0
  years professional; never more than {self.profile.get('current_job.total_experience_years')}.
- Willingness questions (on-call, weekends, overtime, relocation, travel, a test or
  assignment, background check): answer as the PROFILE preferences say, else Yes.
- Reply UNKNOWN only when the answer is a personal fact nothing here states:
  an ID or document number, a reference's contact, a date that is not given.
- Keep it short: a number, a word, or one sentence.

Reply as JSON only: {{"answer": "...", "based_on": "resume|profile|ratings|judgement|unknown"}}

SKILL RATINGS (the candidate's own): {ratings or "none given"}

PROFILE:
{self.profile.facts()}

RESUME:
{resume_text[:6000]}

JOB: {job.get('title', '')} at {job.get('company', '')}
JOB DESCRIPTION (excerpt - what the role needs, to make the answer fit it):
{(job.get('description') or '')[:2000]}

QUESTION: {question}{choices}"""
        text = self._ask(prompt, 150)
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
        answer = str(data.get("answer", "")).strip()
        if not answer or answer.upper() == "UNKNOWN" or data.get("based_on") == "unknown":
            return None
        return answer
