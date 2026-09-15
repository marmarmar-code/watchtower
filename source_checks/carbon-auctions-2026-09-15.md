# EU ETS1 auction-result revisions

The [official EEX auction page](https://www.eex.com/en/markets/environmental-markets/emissions-auctions) supplies a public annual XLSX report. The adapter currently follows its explicit 2026 EU ETS1 file. It does not cover EU ETS2, future report years or real-time secondary-market prices.

The verified workbook contains 156 auctions from 7 January through 15 September 2026, in source zones EU, DE and PL. This corrects an initial exploratory count that omitted the first data row. The table begins at B6 and ends at BI162; the declared worksheet dimension includes four preceding populated rows. Full row continuity and the dynamic final dimension are checked, without hardcoding the current record count.

The sole formula is a decorative HYPERLINK at D3. A bounded in-memory compatibility view validates and removes only that exact formula, then shifts worksheet coordinates past the leading blank row for the existing table reader. The raw workbook remains unchanged. No formula is evaluated; data-table formulas, unknown decorative formulas, archive violations and unexpected worksheet structures fail before persistence.

Workbook metadata explicitly uses the 1900 date system. Date and time cell display formats are checked. Auction dates remain separate from publication time. The source's hh:mm clock is retained without an assumed timezone; the raw serial is also stored. Price is EUR per tCO2, auction volume is tCO2, and total/beneficiary revenues are EUR. Empty amounts remain absent, including beneficiary cells; they are not summed or filled with zero. Other numeric statistics retain source header labels.

Identity combines date, auction name, contract and zone. Successful results require price, volume and total revenue; other source status text is preserved without inferring an outcome from missing amounts. Exact zone selection is optional. First observations are quiet, revisions may alert once, and absence does not establish cancellation. Two complete matching reads are required. Adjacent JSON contains final real-read and code/configuration evidence. Later scheduled operation and actual delivery require separate runtime evidence.
