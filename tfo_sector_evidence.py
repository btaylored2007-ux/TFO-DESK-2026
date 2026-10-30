"""Reviewed issuer evidence mapped to the desk's technology bucket, Sep25.
No ledger rewrite. QQQ uses conservative multi-sector gross upper bounds.
"""
SOURCES = {
 'AMAT':'https://www.appliedmaterials.com/us/en/semiconductor/products.html',
 'AMD':'https://www.amd.com/en/corporate.html',
 'AVGO':'https://www.broadcom.com/company',
 'CDNS':'https://www.cadence.com/en_US/home/resources/factsheets/cadence-design-systems-inc-fs.html',
 'LRCX':'https://www.lamresearch.com/products/our-products/',
 'MRVL':'https://www.marvell.com/content/dam/marvell/en/company/assets/marvell-2024-cdp-disclosure.pdf',
 'NVDA':'https://www.nvidia.com/content/dam/en-zz/Solutions/about-nvidia/corporate-nvidia-in-brief.pdf',
 'SNPS':'https://www.synopsys.com/company.html',
 'SMH':'https://www.vaneck.com/us/en/investments/semiconductor-etf-smh',
 'QQQ':'https://www.invesco.com/qqq-etf/en/about.html',
}


def repaired_sectors(symbol, registered):
    if symbol not in SOURCES:
        return ()
    if symbol == 'QQQ':
        return tuple(sorted(registered))
    return ('technology',)
