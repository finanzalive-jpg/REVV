import asyncio
from playwright.async_api import async_playwright
JOBS=[('bg','bg',{'k':'bg'},(1920,1080),False),('logo','orb',{'k':'orb','s':'240'},(360,360),True),('orb64','orb',{'k':'orb','s':'64'},(96,96),True),
      ('word','word',{'k':'word'},(400,140),True),('tagline','tag',{'k':'tag'},(420,60),True),('ring','ring',{'k':'ring','s':'300'},(340,340),True),('preview','splash',{'k':'splash'},(1920,1080),False)]
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for out,_,q,(w,h),tr in JOBS:
            pg=await b.new_page(viewport={'width':w,'height':h})
            qs='&'.join(f'{a}={c}' for a,c in q.items())
            await pg.goto('file:///home/claude/revv/art/render.html?'+qs);await pg.wait_for_timeout(500)
            el=await pg.query_selector('#v')
            await el.screenshot(path=f'out/{out}.png',omit_background=tr)
            await pg.close()
        await b.close()
asyncio.run(main())
