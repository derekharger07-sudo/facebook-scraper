# TensorDock Deployment - Hybrid Facebook Crawler

## Quick Start (SSH into your TensorDock pod)

### 1. Upload Files
Upload this folder to your TensorDock instance via SFTP or git.

### 2. Install Dependencies
```bash
pip3 install httpx playwright
playwright install chromium
playwright install-deps
```

### 3. Copy Your Proxies
Make sure `proxies.txt` is in the same folder.

### 4. Run Test
```bash
# Test with 10 ads
python3 test_hybrid_crawler.py --keyword "dropshipping" --target 10 --proxy "http://user:pass@proxy:port"

# Test without proxy (if your TensorDock IP is clean)
python3 test_hybrid_crawler.py --keyword "dropshipping" --target 10
```

## Files Included
- `test_hybrid_crawler.py` - The hybrid crawler (Playwright + HTTP)
- `proxies.txt` - Your 50 proxies (copy from Replit)
- `requirements.txt` - Python dependencies

## GPU Usage
The hybrid crawler is CPU-only. GPU is used for:
- Video analysis (separate script)
- AI scoring
- OCR processing
