# TensorDock Deployment Guide

1. **Upload** this folder to your TensorDock instance.
2. **Environment Setup**:
   ```bash
   cp .env.example .env
   # Edit .env with your secrets
   nano .env
   ```
3. **Install**:
   ```bash
   pip3 install -r requirements.txt
   playwright install chromium
   playwright install-deps
   ```
4. **Run**:
   ```bash
   python3 test_hybrid_crawler.py --keyword "winter" --target 10
   ```
