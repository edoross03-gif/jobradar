from . import amazon, ashby, avature, bendingspoons, cornerstone, eightfold, empregoestagios, goldman, greenhouse, htmllinks, intervieweb, jibe, lever, oracle_hcm, personio, phenom, pinpoint, recruitee, smartrecruiters, successfactors, talentsoft, teamtailor, workable, workday

ADAPTERS = {m.NAME: m for m in (greenhouse, lever, ashby, smartrecruiters, workable, recruitee, personio, workday, amazon, successfactors, empregoestagios, eightfold, oracle_hcm, teamtailor, phenom, pinpoint, avature, cornerstone, goldman, bendingspoons, htmllinks, jibe, talentsoft, intervieweb)}

# Adapters that support slug-guessing during `discover`, in probe order (cheapest/most common first).
PROBE_ORDER = [greenhouse, ashby, lever, smartrecruiters, workable, recruitee, personio, teamtailor]

# Query-based adapters: they don't crawl the full board, they search early-career terms.
QUERY_BASED = {"workday", "amazon", "successfactors", "eightfold"}
