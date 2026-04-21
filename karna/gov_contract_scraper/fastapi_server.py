[
    {
        "message": "Government Contract Opportunities API",
        "endpoints": {
            "all_opportunities": "/opportunities",
            "search_opportunities": "/opportunities/search",
            "recent_opportunities": "/opportunities/recent",
            "opportunities_count": "/opportunities/count",
        },
    },
    {"success": True, "count": "len(opportunities)", "data": "opportunities"},
    {
        'app.get("/opportunities/recent': "async def get_recent_opportunities(limit: int = 10):",
        'Get most recent opportunities"': "try:\n        recent_opps = opp_db.get_recent_opportunities(limit=limit)\n        # Convert DataFrame to list of dictionaries\n        opportunities = recent_opps.to_dict(orient='records') if not recent_opps.empty else []\n        return {",
        "success": True,
        "count": "len(opportunities)",
        "data": "opportunities",
    },
    {
        'app.get("/opportunities/count': "async def get_opportunities_count():",
        'Get total count of opportunities"': "try:\n        count = opp_db.get_opportunities_count()\n        return {",
        "success": True,
        "total_opportunities": "count",
    },
    {
        'app.get("/opportunities/search': "async def search_opportunities(q: str",
        "agency": "str = None",
        "days_back": "int = 30):",
        'Search opportunities by keyword"': "try:\n        opportunities = opp_db.query_opportunities(agency=agency",
        "success": True,
        "query": "q",
        "count": "len(opportunities)",
        "data": "opportunities",
    },
    {
        'app.post("/scrape': "async def run_scrape():",
        'Trigger a manual scrape (for testing purposes)"': "try:\n        # This would run the actual scraper\n        # In a real implementation",
        "success": True,
        "message": "Scraping initiated. Check back later for results.",
    },
    {"__main__": "print(", 'host="0.0.0.0': "port=8000)"},
]
