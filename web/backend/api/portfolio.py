# -*- coding: utf-8 -*-
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import sys
import os
import pandas as pd

# Add project root to sys.path to import build_portfolio
# Assuming this file is in web/backend/api
from web.backend.config import PROJECT_ROOT

import build_portfolio

router = APIRouter()

class PortfolioRequest(BaseModel):
    capital: float = 1_000_000
    industry_cap: float = 0.03
    
    # Optional: allow specifying which enriched file to use (e.g. if uploaded newly)
    # For now, default to system default
    
@router.post("/portfolio/generate")
async def generate_portfolio(req: PortfolioRequest):
    """
    Generate portfolio suggestions based on Law of Large Numbers.
    """
    try:
        # Resolve paths
        # If user uploaded a new market file, they likely want to use that.
        # For simplify v1, we assume the system 'market_enriched.csv' is the source of truth,
        # OR we could check if there is a newer file in uploads?
        # Let's stick to the default paths for now as per build_portfolio logic.
        
        config_path = os.path.join(PROJECT_ROOT, "config", "scoring.yaml")
        enriched_path = os.path.join(PROJECT_ROOT, "market_enriched.csv")
        base_path = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
        
        if not os.path.exists(enriched_path):
             return {
                 "status": "error",
                 "message": "Market data not found. Please upload or update data first."
             }

        df = build_portfolio.generate_portfolio(
            capital=req.capital,
            ind_cap=req.industry_cap,
            config_path=config_path,
            enriched_path=enriched_path,
            base_path=base_path
        )
        
        if df.empty:
             return {
                 "status": "warning",
                 "message": "No stocks met the criteria.",
                 "data": []
             }
             
        # Convert to list of dicts for JSON
        result_list = df.to_dict(orient="records")
        
        # Calculate summary
        total_invest = df["suggested_amt"].sum()
        total_ratio = total_invest / req.capital
        
        summary = {
            "total_capital": req.capital,
            "total_invested": total_invest,
            "invest_ratio": round(total_ratio, 4),
            "cash_reserved": req.capital - total_invest,
            "industries_count": len(df),
            "is_compliant": total_ratio <= 0.67
        }
        
        return {
            "status": "success",
            "summary": summary,
            "data": result_list
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
