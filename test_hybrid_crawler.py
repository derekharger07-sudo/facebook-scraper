#!/usr/bin/env python3
"""
🔀 HYBRID FACEBOOK AD LIBRARY CRAWLER (STANDALONE for TensorDock)
- Playwright for bootstrap (bypass anti-bot)
- HTTP for fast pagination (low resource usage)
- Best of both worlds: ~90% HTTP, ~10% browser

DEPLOY TO TENSORDOCK:
  pip3 install httpx playwright
  playwright install chromium
  playwright install-deps
  python3 test_hybrid_crawler.py --keyword "dropshipping" --target 10 --proxy "http://user:pass@ip:port"
"""

import os
import sys
import json
import re
import time
import asyncio
import httpx
from pathlib import Path
from typing import Optional, Dict, Any, List
from playwright.async_api import async_playwright
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
TARGET_ADS = 50
ADS_PER_PAGE = 30
MAX_RETRIES = 5
RETRY_DELAY = 10  # seconds before retry
RATE_LIMIT_DELAY = 3.0  # seconds between HTTP requests (conservative)
POST_BOOTSTRAP_DELAY = 5  # seconds to wait after bootstrap before API calls
OUTPUT_DIR = Path(__file__).parent / "out"  # Standalone - outputs to ./out/

# GraphQL endpoint
GRAPHQL_URL = "https://www.facebook.com/api/graphql/"
DOC_ID = "25464068859919530"

# Browser headers to reuse
BASE_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",  # No compression - simpler handling
    "Origin": "https://www.facebook.com",
    "Referer": "https://www.facebook.com/ads/library/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


class HybridFacebookCrawler:
    """Hybrid crawler: Playwright bootstrap + HTTP pagination"""
    
    def __init__(self, proxy: Optional[str] = None):
        self.proxy = proxy
        self.cookies = {}
        self.cookie_header = ""
        self.fb_dtsg = None
        self.lsd = None
        self.jazoest = None
        self.user_agent = None
        self.http_client = None
        
    async def playwright_bootstrap(self, keyword: str) -> bool:
        """
        Use Playwright to bootstrap - gets past anti-bot protection
        Extracts cookies and tokens for HTTP requests
        """
        logger.info(f"🌐 Playwright Bootstrap: '{keyword}'")
        
        url = f"https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=US&media_type=all&q={keyword}&search_type=keyword_unordered"
        
        try:
            async with async_playwright() as p:
                # Launch browser with optional proxy
                launch_args = {
                    "headless": True,
                    "args": ["--disable-blink-features=AutomationControlled"]
                }
                
                if self.proxy:
                    # Parse proxy URL
                    proxy_parts = self.proxy.replace("http://", "").split("@")
                    if len(proxy_parts) == 2:
                        auth, host = proxy_parts
                        user, pwd = auth.split(":")
                        launch_args["proxy"] = {
                            "server": f"http://{host}",
                            "username": user,
                            "password": pwd
                        }
                
                browser = await p.chromium.launch(**launch_args)
                context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
                
                page = await context.new_page()
                
                # Navigate and wait for page to load
                logger.info("📄 Loading Ad Library page...")
                await page.goto(url, wait_until="networkidle", timeout=30000)
                
                # Wait a bit for JS to execute
                await asyncio.sleep(2)
                
                # Get cookies
                cookies = await context.cookies()
                self.cookies = {c["name"]: c["value"] for c in cookies}
                self.cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                logger.info(f"🍪 Got {len(cookies)} cookies")
                
                # Get page content and extract tokens
                html = await page.content()
                
                # Extract fb_dtsg
                self.fb_dtsg = (
                    self._extract_token(html, r'"DTSGInitialData".*?"token":"([^"]+)"') or
                    self._extract_token(html, r'"dtsg":{"token":"([^"]+)"') or
                    self._extract_token(html, r'{"name":"fb_dtsg","value":"([^"]+)"}')
                )
                
                # Extract lsd
                self.lsd = (
                    self._extract_token(html, r'"LSD".*?"token":"([^"]+)"') or
                    self._extract_token(html, r'{"name":"lsd","value":"([^"]+)"}')
                )
                
                # Extract jazoest
                self.jazoest = self._extract_token(html, r'"jazoest":"(\d+)"')
                
                # Get user agent
                self.user_agent = await page.evaluate("navigator.userAgent")
                
                await browser.close()
                
                if self.fb_dtsg and self.lsd:
                    logger.info(f"✅ Bootstrap success! fb_dtsg={self.fb_dtsg[:20]}..., lsd={self.lsd[:10]}...")
                    logger.info(f"⏳ Waiting {POST_BOOTSTRAP_DELAY}s before API calls...")
                    await asyncio.sleep(POST_BOOTSTRAP_DELAY)
                    return True
                else:
                    logger.warning(f"⚠️ Missing tokens: fb_dtsg={bool(self.fb_dtsg)}, lsd={bool(self.lsd)}")
                    # Save HTML for debugging
                    debug_file = OUTPUT_DIR / "debug_bootstrap.html"
                    OUTPUT_DIR.mkdir(exist_ok=True)
                    with open(debug_file, "w") as f:
                        f.write(html[:50000])
                    logger.info(f"💾 Saved bootstrap HTML to {debug_file}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Playwright bootstrap error: {e}")
            return False
    
    def _extract_token(self, html: str, pattern: str) -> Optional[str]:
        """Extract token from HTML using regex"""
        match = re.search(pattern, html)
        return match.group(1) if match else None
    
    def _create_http_client(self) -> httpx.Client:
        """Create HTTP client with browser cookies"""
        headers = {
            **BASE_HEADERS,
            "User-Agent": self.user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Cookie": self.cookie_header,
        }
        
        return httpx.Client(
            headers=headers,
            timeout=30.0,
            follow_redirects=True
        )
    
    def fetch_ads_page(self, keyword: str, cursor: Optional[str] = None) -> Dict[str, Any]:
        """
        Fetch ads page using HTTP (fast, low resource)
        Uses tokens from Playwright bootstrap
        """
        if not self.http_client:
            self.http_client = self._create_http_client()
        
        # Build variables
        variables = {
            "queryString": keyword,
            "country": "US",
            "activeStatus": "ACTIVE",
            "adType": "ALL",
            "mediaType": "ALL",
            "first": ADS_PER_PAGE,
            "cursor": cursor
        }
        
        # Build form data
        form_data = {
            "fb_dtsg": self.fb_dtsg,
            "lsd": self.lsd,
            "fb_api_req_friendly_name": "AdLibrarySearchPaginationQuery",
            "doc_id": DOC_ID,
            "variables": json.dumps(variables)
        }
        
        if self.jazoest:
            form_data["jazoest"] = self.jazoest
        
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-FB-Friendly-Name": "AdLibrarySearchPaginationQuery",
            "X-FB-LSD": self.lsd or "",
        }
        
        for attempt in range(MAX_RETRIES):
            try:
                time.sleep(RATE_LIMIT_DELAY)
                
                response = self.http_client.post(
                    GRAPHQL_URL,
                    data=form_data,
                    headers=headers
                )
                
                if response.status_code == 429:
                    wait = RETRY_DELAY * (2 ** attempt)
                    logger.warning(f"⏳ Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                
                if response.status_code >= 500:
                    wait = RETRY_DELAY * (2 ** attempt)
                    logger.warning(f"⏳ Server error {response.status_code}, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                
                if response.status_code == 403:
                    logger.warning("⚠️ Got 403 - tokens may have expired, need re-bootstrap")
                    return {"ads": [], "next_cursor": None, "error": "token_expired"}
                
                if response.status_code != 200:
                    return {"ads": [], "next_cursor": None, "error": f"HTTP {response.status_code}"}
                
                # Parse response - handle encoding issues
                try:
                    data = response.json()
                except Exception:
                    # Try to decode manually if auto-decode fails
                    import gzip
                    import zlib
                    try:
                        content = gzip.decompress(response.content)
                        data = json.loads(content.decode('utf-8'))
                    except:
                        try:
                            content = zlib.decompress(response.content, 16 + zlib.MAX_WBITS)
                            data = json.loads(content.decode('utf-8'))
                        except:
                            # Last resort: try raw content
                            data = json.loads(response.content.decode('utf-8', errors='ignore'))
                
                # Check for errors in response
                if "errors" in data:
                    errors = data['errors']
                    # Check if it's a rate limit error
                    is_rate_limit = any(e.get('code') == 1675004 for e in errors)
                    if is_rate_limit:
                        wait = RETRY_DELAY * (2 ** attempt)
                        logger.warning(f"⏳ Rate limited, waiting {wait}s before retry...")
                        time.sleep(wait)
                        continue
                    logger.error(f"GraphQL errors: {errors}")
                    return {"ads": [], "next_cursor": None, "error": "graphql_error"}
                
                # Navigate to ads
                search_results = data.get("data", {}).get("ad_library_main", {}).get("search_results_connection", {})
                edges = search_results.get("edges", [])
                page_info = search_results.get("page_info", {})
                
                ads = []
                for edge in edges:
                    node = edge.get("node", {})
                    collated_results = node.get("collated_results", [])
                    
                    for ad in collated_results:
                        ads.append({
                            "ad_archive_id": ad.get("ad_archive_id"),
                            "page_name": ad.get("page_name"),
                            "page_id": ad.get("page_id"),
                            "start_date": ad.get("start_date"),
                            "end_date": ad.get("end_date"),
                            "is_active": ad.get("is_active"),
                            "snapshot": ad
                        })
                
                next_cursor = page_info.get("end_cursor")
                has_next = page_info.get("has_next_page", False)
                
                logger.info(f"📄 HTTP fetch: {len(ads)} ads, has_next={has_next}")
                
                return {
                    "ads": ads,
                    "next_cursor": next_cursor if has_next else None,
                    "error": None
                }
                
            except Exception as e:
                logger.error(f"HTTP fetch error: {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
                    continue
                return {"ads": [], "next_cursor": None, "error": str(e)}
        
        return {"ads": [], "next_cursor": None, "error": "max_retries"}
    
    async def scrape(self, keyword: str, target_count: int = TARGET_ADS) -> List[Dict]:
        """Main scrape: Playwright bootstrap + HTTP pagination"""
        logger.info(f"🚀 Hybrid scrape: '{keyword}' (target: {target_count})")
        
        # Step 1: Playwright bootstrap
        if not await self.playwright_bootstrap(keyword):
            logger.error("❌ Bootstrap failed")
            return []
        
        # Step 2: HTTP pagination
        all_ads = []
        cursor = None
        page = 0
        
        while len(all_ads) < target_count:
            page += 1
            logger.info(f"📖 HTTP page {page}...")
            
            result = self.fetch_ads_page(keyword, cursor)
            
            if result.get("error") == "token_expired":
                logger.warning("🔄 Re-bootstrapping...")
                if await self.playwright_bootstrap(keyword):
                    continue
                else:
                    break
            
            if result.get("error"):
                logger.error(f"❌ Error: {result['error']}")
                break
            
            ads = result.get("ads", [])
            if not ads:
                logger.info("📭 No more ads")
                break
            
            all_ads.extend(ads)
            logger.info(f"📊 Total: {len(all_ads)} ads")
            
            cursor = result.get("next_cursor")
            if not cursor:
                break
        
        return all_ads[:target_count]
    
    def close(self):
        """Cleanup"""
        if self.http_client:
            self.http_client.close()


async def run_test(keyword: str = "dropshipping", target: int = TARGET_ADS, proxy: Optional[str] = None):
    """Run hybrid crawler test"""
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    crawler = HybridFacebookCrawler(proxy=proxy)
    
    try:
        start = time.time()
        ads = await crawler.scrape(keyword, target)
        elapsed = time.time() - start
        
        # Save results
        output_file = OUTPUT_DIR / f"hybrid_ads_{len(ads)}.json"
        with open(output_file, "w") as f:
            json.dump({
                "keyword": keyword,
                "count": len(ads),
                "elapsed_seconds": round(elapsed, 2),
                "ads_per_second": round(len(ads) / elapsed, 2) if elapsed > 0 else 0,
                "method": "hybrid",
                "ads": ads
            }, f, indent=2)
        
        logger.info(f"💾 Saved {len(ads)} ads to {output_file}")
        logger.info(f"⏱️ Time: {elapsed:.2f}s ({len(ads)/elapsed:.2f} ads/sec)")
        
        return ads
        
    finally:
        crawler.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Hybrid Facebook Crawler Test")
    parser.add_argument("--keyword", "-k", default="dropshipping", help="Keyword")
    parser.add_argument("--target", "-t", type=int, default=50, help="Target ads")
    parser.add_argument("--proxy", "-p", help="Proxy URL")
    
    args = parser.parse_args()
    
    print(f"""
╔═══════════════════════════════════════════════════════════╗
║  🔀 HYBRID FACEBOOK CRAWLER TEST                          ║
║  Playwright Bootstrap + HTTP Pagination                   ║
║  Keyword: {args.keyword:<45} ║
║  Target: {args.target} ads                                       ║
╚═══════════════════════════════════════════════════════════╝
""")
    
    ads = asyncio.run(run_test(args.keyword, args.target, args.proxy))
    
    if ads:
        print(f"\n✅ SUCCESS: {len(ads)} ads collected")
        print("\nSample ad:")
        sample = ads[0]
        print(f"  - ad_archive_id: {sample.get('ad_archive_id')}")
        print(f"  - page_name: {sample.get('page_name')}")
        print(f"  - start_date: {sample.get('start_date')}")
    else:
        print("\n❌ FAILED: No ads collected")
