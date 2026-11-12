from langchain.tools import tool
import requests
from bs4 import BeautifulSoup
import os
from rich import print
from dotenv import load_dotenv

from app.telemetry import trace_span
load_dotenv()

# TD-RUNTIME-03 fix: Tavily client is now lazy-initialised inside each call.
# Instantiating at module level crashed the import when TAVILY_API_KEY was absent.
_tavily_client = None


def _get_tavily():
    """Return a cached TavilyClient, or raise a clear error if the key is missing."""
    global _tavily_client
    if _tavily_client is None:
        from tavily import TavilyClient
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "TAVILY_API_KEY is not set. "
                "Tavily web search is unavailable."
            )
        _tavily_client = TavilyClient(api_key=api_key)
    return _tavily_client


@tool
def web_search(query : str) -> str:
    """
    Search the web for recent and reliable information on a topic. Return Title , URLs and Snippets
    """
    with trace_span("search.tavily", attributes={"search.query": query[:60]}):
        try:
            client = _get_tavily()
        except EnvironmentError as e:
            return f"Web search unavailable: {e}"
        result=client.search(query=query,max_results=5)
        out=[]
        for r in result["results"]:
            out.append(
                f"Title: {r['title']}\nURL: {r['url']}\nSnippet:{r['content'][:300]}\r"
            )
        return "\n-----\n".join(out)


@tool
def scrape_url(url:str)->str:
    """Scrape and return clean text content from a given URL for deeper reading.
    """
    with trace_span("search.scrape", attributes={"scrape.url": url[:100]}):
        try:
            resp=requests.get(url,timeout=8,headers={"User-Agent":"Mozilla/5.0"})
            soup=BeautifulSoup(resp.text,"html.parser")
            for tag in soup(["script","style","nav","footer"]):
                tag.decompose()
            return soup.get_text(separator=" ",strip=True)[:3000]
        except Exception as e:
            return f"could not scrape URL:{str(e)}"


@tool
def multi_source_search(query: str) -> str:
    """
    Search across multiple sources simultaneously (Tavily, arXiv, Wikipedia,
    DuckDuckGo, and optionally NewsAPI / Google Scholar).
    Returns deduplicated results from all available sources.
    """
    with trace_span("search.multi_source", attributes={"search.query": query[:60]}):
        from search_sources import SearchOrchestrator
        orchestrator = SearchOrchestrator()
        results = orchestrator.search(query, max_results_per_source=3)
        return SearchOrchestrator.format_results(results)



if __name__ == "__main__":
    print(web_search.invoke("What is the recent news of iran war"))
    print(scrape_url.invoke("https://www.nbcnews.com/world/iran-war"))