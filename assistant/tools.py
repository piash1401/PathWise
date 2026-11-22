"""
Deterministic PathWise tools used by the assistant.

Every number in here comes from the existing backend engines
(opportunities.eligibility, opportunities.recommendations, costs.services).
The language model never calculates eligibility or costs; it only explains
the text these functions produce.
"""

from costs.services import CostDataMissing, estimate_cost
from opportunities.eligibility import check_eligibility
from opportunities.models import Opportunity
from opportunities.recommendations import recommend
from applications.models import Application


def bdt(value):
    return f'৳{int(round(float(value or 0))):,}'


def _source(kind, title, text, url='', status='verified', verified_on=None):
    return {
        'kind': kind, 'title': title, 'text': text.strip(), 'url': url,
        'status': status, 'verified_on': verified_on,
    }


# ------------------------------------------------------------------ profile
def profile_summary(profile):
    """Only study-relevant facts. No name, email, phone or institution address."""
    english = f'{profile.english_test} {profile.overall_score}' if profile.english_test and profile.overall_score \
        else 'not provided'
    lines = [
        f'Degree: {profile.highest_degree or "not provided"}; field: {profile.field_of_study or "not provided"}',
        f'CGPA: {profile.grade}/{profile.grade_scale}' if profile.grade is not None else 'CGPA: not provided',
        f'English test: {english}',
        f'Annual budget: {bdt(profile.annual_budget_bdt)}' if profile.annual_budget_bdt else 'Annual budget: not provided',
        f'Preferred destinations: {", ".join(profile.destination_preferences) or "not provided"}',
        f'Target field: {profile.target_field or "not provided"}',
        f'Skills: {", ".join(profile.skills[:12]) or "not provided"}',
        f'Work experience: {profile.work_experience_months} months',
    ]
    return _source('Your PathWise profile', 'Your profile', '\n'.join(f'- {l}' for l in lines))


def missing_profile_fields(profile):
    missing = []
    if not profile.annual_budget_bdt:
        missing.append('annual budget')
    if not profile.english_test or not profile.overall_score:
        missing.append('English test score')
    if not profile.destination_preferences:
        missing.append('preferred destination')
    if not profile.target_field:
        missing.append('target field of study')
    return missing


# ----------------------------------------------------------- recommendations
def tool_recommendations(profile, countries=(), limit=5):
    queryset = Opportunity.objects.filter(is_active=True, opportunity_type='program')
    if len(countries) == 1:
        queryset = queryset.filter(country=countries[0])
    results = recommend(profile, queryset, limit)
    if not results:
        return [_source('PathWise recommendation engine', 'Program matches',
                        'No programs are stored for this selection yet.')]

    lines = ['Ranked by the PathWise engine (eligibility 40, budget 25, destination 15, field 20). '
             'Scores are guidance, not admission chances.']
    for i, r in enumerate(results, start=1):
        cost = r['cost']
        money = f' Estimated cost {bdt(cost["per_year_bdt"])} per year.' if cost else ''
        lines.append(
            f'{i}. {r["title"]}, {r["institution"]} ({r["country"]}): score {r["score"]}/100 '
            f'({r["label"]}). Eligibility: {r["eligibility_status"]}.{money} '
            f'Notes: {" ".join(r["reasons"])}'
            f'{" Record not yet verified." if r["verification_status"] != "verified" else ""}'
        )
    return [_source('PathWise recommendation engine', 'Your best program matches', '\n'.join(lines))]


# -------------------------------------------------------------- eligibility
def tool_eligibility(profile, opportunity):
    result = check_eligibility(profile, opportunity)
    lines = [f'Overall: {result["overall_status"]}.']
    for item in result['criteria_results']:
        required = item.get('required_value')
        lines.append(f'- {item["criterion"]}: {item["status"]}'
                     + (f' (required: {required})' if required not in (None, '', []) else ''))
    lines.append('"Unknown" means the program rules are not stored yet or need a manual check on the official page.')
    return _source('PathWise eligibility engine', f'Eligibility: {opportunity.title}, {opportunity.institution}',
                   '\n'.join(lines), opportunity.source_url,
                   opportunity.verification_status, _date(opportunity.verified_on))


# --------------------------------------------------------------------- cost
def tool_cost(profile, opportunity=None, country=None):
    try:
        e = estimate_cost(opportunity=opportunity, country=country, profile=profile)
    except CostDataMissing as error:
        return _source('PathWise cost engine', 'Cost estimate', f'No cost estimate is available: {error}',
                       status='pending')

    label = f'{e["opportunity"]}, {e["institution"]}' if e['opportunity'] else e['country']
    parts = ', '.join(f'{i["label"].split(" (")[0]} {bdt(i["bdt"])} [{i["basis"]}]' for i in e['breakdown'])
    p = e['part_time']
    lines = [
        f'Cost estimate for {label}, {e["duration_months"]} months.',
        f'- Total: {bdt(e["total_bdt"])} ({bdt(e["per_year_bdt"])} per year).',
        f'- Breakdown: {parts}.',
        f'- Part-time work: {p["hours_per_week"]:g} h/week (legal maximum {p["max_hours_per_week"]}), '
        f'about {bdt(p["net_bdt"])} after tax; covers {p["covers_total_percent"]}% of the total '
        f'and {p["covers_living_percent"]}% of living costs.',
        f'- Still to fund after part-time income: {bdt(e["net_after_part_time_bdt"])}.',
        f'- Visa proof of funds (first year): {bdt(e["proof_of_funds"]["total_bdt"])}.',
        f'- Exchange rate: 1 {e["currency"]} = ৳{e["exchange_rate"]["bdt_per_unit"]} '
        f'(as of {e["exchange_rate"]["as_of"]}).',
    ]
    f = e['funding']
    if f:
        if f['status'] == 'shortfall':
            lines.append(f'- Against the student budget ({bdt(f["budget_for_whole_program_bdt"])} for the program): '
                         f'shortfall of {bdt(f["gap_bdt"])}.')
        else:
            lines.append(f'- Against the student budget ({bdt(f["budget_for_whole_program_bdt"])} for the program): '
                         f'within budget, surplus {bdt(f["surplus_bdt"])}.')
    if e['assumed_items']:
        lines.append('- Planning assumptions (not official figures): ' + ', '.join(e['assumed_items']) + '.')
    for w in e['warnings']:
        lines.append(f'- Warning: {w}')
    source_url = e['sources'][0]['url'] if e['sources'] else (opportunity.source_url if opportunity else '')
    return _source('PathWise cost engine', f'Cost estimate: {label}', '\n'.join(lines), source_url,
                   'verified' if e['verified_on'] else 'pending', e['verified_on'])


# ------------------------------------------------------------------ tracker
def tool_tracker(user):
    apps = Application.objects.filter(user=user).select_related('opportunity')
    if not apps:
        return _source('Your application tracker', 'Your applications',
                       'The tracker is empty. The student has not added any program yet.')
    lines = []
    for a in apps:
        deadline = a.deadline_override or a.opportunity.deadline
        todo = [i['label'] for i in (a.checklist or []) if not i.get('done')]
        done = len(a.checklist or []) - len(todo)
        lines.append(
            f'- {a.opportunity.title}, {a.opportunity.institution}: status {a.get_status_display()}; '
            f'deadline {deadline.isoformat() if deadline else "not set"}; documents {done}/{len(a.checklist or [])}'
            + (f'; still to do: {"; ".join(todo[:4])}' if todo else '')
        )
    return _source('Your application tracker', 'Your applications', '\n'.join(lines))


# ------------------------------------------------------------------ compare
def tool_compare(profile, opportunities):
    sources = []
    for opp in opportunities[:2]:
        sources.append(tool_cost(profile, opportunity=opp))
        if profile is not None:
            sources.append(tool_eligibility(profile, opp))
    return sources


def _date(value):
    return value.isoformat() if value else None
