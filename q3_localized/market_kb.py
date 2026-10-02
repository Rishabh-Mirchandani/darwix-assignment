"""Market knowledge bases for the Philippines and Indonesia bots.

**Provenance, stated plainly:** unlike the India KB -- which is scraped from a
real insurer's public site with full URL citations -- these records are
hand-authored and illustrative. They encode real, publicly documented market
mechanics (PH grace periods under the Insurance Code, Indonesian multifinance
denda and keringanan practice), but they are not scraped from a named company
and must not be presented as any specific insurer's terms. Every record's
`source` says so, and the limitation is repeated in the write-up. Fabricating
a citation to a real company would be worse than having none.

**Why `content` is English while `answer_text` is local.**
Retrieval runs on `bge-small-en-v1.5`, an English-only embedding model.
Embedding Tagalog or Indonesian text with it produces poor neighbourhoods and
retrieval silently degrades. So each record carries:

    content      -> English. Embedded, BM25-indexed, searched.
    answer_text  -> Taglish / Bahasa Indonesia. What the bot actually says.

The agent is instructed to search in English and speak locally. This separates
the retrieval language from the delivery language, which is what lets a small
English embedding model serve a non-English voice bot without either
translating at runtime or swapping in a heavier multilingual model that this
machine cannot comfortably host. The tradeoff -- a caller's exact local
phrasing never reaches the index directly -- is noted as a limitation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import settings                      # noqa: E402
from q2_kb.index.schema import KBRecord, make_record_id  # noqa: E402

PH_SOURCE = "synthetic / PH bancassurance market practice (illustrative)"
ID_SOURCE = "synthetic / ID multifinance market practice (illustrative)"


def _rec(
    market: str, category: str, title: str, content: str, answer: str,
    source: str, terms: list[str],
) -> KBRecord:
    return KBRecord(
        record_id=make_record_id(f"{market}_{category}", title, 0),
        title=title,
        content=content,
        answer_text=answer,
        category=category,          # type: ignore[arg-type]
        market=market,              # type: ignore[arg-type]
        source=source,
        source_url=f"internal://{market}/{category}",
        source_type="website",
        version="1.0",
        pii=False,
        canonical_terms=terms,
        section_kind="faq",
        token_estimate=len(content.split()) * 4 // 3,
    )


# ---------------------------------------------------------------------------
# Philippines - life insurance / bancassurance
# ---------------------------------------------------------------------------

PH_RECORDS = [
    _rec("ph", "coverage_rule", "Grace period for premium payment",
         "Life insurance policies carry a grace period of 30 days from the "
         "premium due date. The policy stays in force during the grace period. "
         "If the premium is unpaid when the grace period ends, the policy lapses.",
         "May 30 days po kayong grace period from the due date. Active pa rin "
         "po ang policy ninyo within that period. Pag lumagpas po, saka lang "
         "mag-la-lapse.",
         PH_SOURCE, ["grace period", "lapse"]),

    _rec("ph", "coverage_rule", "What happens when a policy lapses",
         "A lapsed policy no longer provides coverage. Claims filed while the "
         "policy is lapsed are not payable. The policy may be reinstated within "
         "a set period by paying overdue premiums, sometimes with evidence of "
         "insurability.",
         "Pag na-lapse po, wala na pong coverage, at hindi po mababayaran ang "
         "claims. Pwede pa po i-reinstate within a certain period — babayaran "
         "lang po ang overdue premiums.",
         PH_SOURCE, ["lapse"]),

    _rec("ph", "eligibility_rule", "Reinstatement of a lapsed policy",
         "Reinstatement is generally allowed within two to three years of "
         "lapse. It requires payment of all overdue premiums with interest, "
         "and the insurer may require updated health declarations.",
         "Pwede po i-reinstate within two to three years ng lapse. Kailangan "
         "lang po bayaran lahat ng overdue premiums, at minsan may health "
         "declaration po ulit.",
         PH_SOURCE, ["lapse"]),

    _rec("ph", "coverage_rule", "Changing a beneficiary",
         "A policyholder may change a revocable beneficiary at any time by "
         "submitting a change-of-beneficiary form. An irrevocable beneficiary "
         "cannot be changed without that beneficiary's written consent.",
         "Pwede po ninyong palitan ang beneficiary anytime kung revocable — "
         "may form lang po. Pero kung irrevocable po, kailangan po ng consent "
         "ng beneficiary mismo.",
         PH_SOURCE, []),

    _rec("ph", "product_plan", "What a rider adds to a policy",
         "A rider is an add-on benefit attached to a base life policy, such as "
         "critical illness, accidental death, or waiver of premium. Riders "
         "increase the total premium and have their own terms.",
         "Ang rider po ay add-on sa base policy ninyo — tulad po ng critical "
         "illness, accidental death, o waiver of premium. May dagdag po siya "
         "sa premium, at may sarili pong terms.",
         PH_SOURCE, []),

    _rec("ph", "coverage_rule", "Waiver of premium rider",
         "A waiver of premium rider keeps the policy in force without further "
         "premium payments if the insured becomes totally and permanently "
         "disabled, subject to the rider's definitions and waiting period.",
         "Kung may waiver of premium rider po kayo, tuloy pa rin po ang policy "
         "kahit hindi na kayo makapagbayad, kung ma-totally and permanently "
         "disabled po kayo — depende po sa terms ng rider.",
         PH_SOURCE, []),

    _rec("ph", "claims_process", "Filing a death claim",
         "A death claim requires the original policy contract, a certified "
         "death certificate, claimant identification, and a completed claim "
         "form. Processing typically begins once documents are complete.",
         "Para po sa death claim, kailangan po ang original policy, certified "
         "death certificate, valid ID ng claimant, at claim form. Magsisimula "
         "po ang processing pag kumpleto na po lahat.",
         PH_SOURCE, []),

    _rec("ph", "coverage_rule", "Contestability period",
         "Most life policies have a two-year contestability period from issue "
         "or reinstatement. Within this period the insurer may contest a claim "
         "on grounds of material misrepresentation in the application.",
         "May two-year contestability period po ang karamihan ng policies. "
         "Within po ng period na yan, pwede pong i-contest ang claim kung may "
         "maling na-declare sa application.",
         PH_SOURCE, []),

    _rec("ph", "service_policy", "Premium payment channels",
         "Premiums may be paid through the partner bank over the counter, via "
         "online banking, through auto-debit arrangement, or at accredited "
         "payment centres. Auto-debit reduces the risk of accidental lapse.",
         "Pwede po kayong magbayad sa bangko over the counter, online banking, "
         "auto-debit, o sa mga payment centers. Mas safe po ang auto-debit para "
         "hindi po kayo ma-lapse.",
         PH_SOURCE, []),

    _rec("ph", "objection_handling", "Value of coverage versus cost",
         "Life insurance transfers the financial impact of an untimely death "
         "from the family to the insurer. The benefit is paid as a lump sum to "
         "beneficiaries and is generally not subject to the delays of estate "
         "settlement.",
         "Naiintindihan ko po na gastos siya monthly. Pero ang life insurance "
         "po kasi, siya ang sasalo sa pamilya ninyo kung may mangyari. Direkta "
         "po sa beneficiary ang proceeds, hindi po kailangang maghintay ng "
         "estate settlement.",
         PH_SOURCE, []),

    _rec("ph", "objection_handling", "Already covered by employer insurance",
         "Employer-provided group life cover usually ends when employment ends "
         "and is typically a multiple of salary rather than a needs-based "
         "amount. A personal policy stays with the policyholder across jobs.",
         "Okay po yan, pero yung group life po sa trabaho, natatapos po pag "
         "nag-resign o nag-transfer kayo. Yung personal policy po, sa inyo po "
         "talaga yan kahit magpalit kayo ng trabaho.",
         PH_SOURCE, []),

    _rec("ph", "eligibility_rule", "Policy loan availability",
         "Policies with accumulated cash value may allow a policy loan. The "
         "loan accrues interest and any unpaid balance is deducted from the "
         "death benefit or surrender value.",
         "Kung may cash value na po ang policy ninyo, pwede po kayong "
         "mag-policy loan. May interest lang po, at mababawas po sa benefit "
         "kung hindi mabayaran.",
         PH_SOURCE, []),
]


# ---------------------------------------------------------------------------
# Indonesia - multifinance / consumer financing
# ---------------------------------------------------------------------------

ID_RECORDS = [
    _rec("id", "coverage_rule", "Late payment penalty",
         "A late fee is charged per day of delay on an overdue instalment, "
         "calculated as a percentage of the outstanding instalment amount. The "
         "exact rate is stated in the financing agreement.",
         "Untuk keterlambatan, ada denda harian ya Pak, dihitung dari nilai "
         "angsuran yang tertunggak. Nominal persisnya tercantum di perjanjian "
         "pembiayaan Bapak.",
         ID_SOURCE, []),

    _rec("id", "coverage_rule", "Instalment due date and grace",
         "The instalment due date is fixed each month per the financing "
         "agreement. Payment received after the due date is treated as late and "
         "incurs a penalty from the first day of delay.",
         "Jatuh tempo cicilan Bapak tetap setiap bulan sesuai perjanjian. "
         "Kalau pembayaran masuk setelah tanggal itu, dihitung telat dan kena "
         "denda mulai hari pertama ya.",
         ID_SOURCE, []),

    _rec("id", "eligibility_rule", "Payment relief and restructuring",
         "Customers experiencing documented financial hardship may apply for "
         "relief, which can include rescheduling the tenor or temporarily "
         "reducing the instalment. Approval requires assessment and supporting "
         "documents; it is not automatic.",
         "Kalau memang kondisinya sedang berat, Bapak bisa mengajukan "
         "keringanan — bisa perpanjangan tenor atau penyesuaian angsuran "
         "sementara. Tapi perlu diajukan dan dinilai dulu ya Pak, tidak "
         "otomatis disetujui.",
         ID_SOURCE, []),

    _rec("id", "claims_process", "How to apply for relief",
         "A relief application is submitted through customer service with proof "
         "of the hardship, such as a termination letter or medical records. The "
         "assessment outcome is communicated in writing.",
         "Pengajuan keringanan lewat customer service ya Pak, dengan bukti "
         "pendukung seperti surat PHK atau dokumen medis. Nanti hasilnya "
         "diinformasikan tertulis.",
         ID_SOURCE, []),

    _rec("id", "coverage_rule", "Early settlement",
         "A customer may settle the remaining balance early. An early "
         "settlement fee may apply, and the payoff amount must be requested "
         "from customer service as it changes daily.",
         "Bisa Pak, pelunasan dipercepat diperbolehkan. Biasanya ada biaya "
         "pelunasan dipercepat, dan nominalnya harus diminta ke customer "
         "service karena berubah tiap hari.",
         ID_SOURCE, []),

    _rec("id", "service_policy", "Payment channels",
         "Instalments can be paid by bank transfer to a virtual account, "
         "through partner minimarkets, via mobile banking, or by auto-debit. "
         "Virtual account transfers are credited fastest.",
         "Pembayaran bisa lewat transfer ke virtual account, minimarket, "
         "mobile banking, atau auto-debit Pak. Paling cepat masuk itu transfer "
         "ke virtual account.",
         ID_SOURCE, []),

    _rec("id", "service_policy", "Payment not yet reflected",
         "A payment may take one to two business days to reflect, depending on "
         "the channel used. Customers should keep the transfer receipt and "
         "report it to customer service if it has not appeared.",
         "Kadang pembayaran butuh satu sampai dua hari kerja untuk masuk, "
         "tergantung channel-nya Pak. Mohon simpan bukti transfernya, nanti "
         "bisa dilaporkan ke customer service kalau belum muncul.",
         ID_SOURCE, []),

    _rec("id", "coverage_rule", "Tenor and instalment relationship",
         "A longer tenor lowers the monthly instalment but increases the total "
         "amount paid over the financing period. A shorter tenor raises the "
         "monthly instalment and lowers the total cost.",
         "Kalau tenornya lebih panjang, cicilan bulanannya lebih ringan, tapi "
         "total yang dibayar jadi lebih besar. Kalau tenor pendek, cicilan "
         "lebih besar tapi totalnya lebih hemat.",
         ID_SOURCE, []),

    _rec("id", "eligibility_rule", "Down payment requirement",
         "A down payment is required at the start of financing. The percentage "
         "depends on the asset type and the customer's credit assessment.",
         "DP memang disyaratkan di awal pembiayaan Pak. Besarannya tergantung "
         "jenis unit dan hasil penilaian kredit Bapak.",
         ID_SOURCE, []),

    _rec("id", "claims_process", "Documents held during financing",
         "The ownership document is held by the financing company until the "
         "financing is fully settled. It is released to the customer after "
         "final payment and administrative processing.",
         "Dokumen kepemilikan disimpan perusahaan pembiayaan sampai lunas ya "
         "Pak. Setelah pelunasan dan proses administrasi selesai, baru "
         "diserahkan ke Bapak.",
         ID_SOURCE, []),

    _rec("id", "objection_handling", "Dispute over penalty amount",
         "Late fees accrue daily from the first day after the due date, so a "
         "penalty can grow larger than expected if several instalments are "
         "missed. The calculation basis is set out in the financing agreement.",
         "Dendanya dihitung harian dari hari pertama setelah jatuh tempo Pak, "
         "jadi kalau beberapa bulan tertunggak memang bisa terasa besar. Cara "
         "hitungnya ada di perjanjian pembiayaan.",
         ID_SOURCE, []),

    _rec("id", "service_policy", "Updating contact details",
         "Customers should update their phone number and address with customer "
         "service so that payment reminders and statements are received on "
         "time.",
         "Kalau nomor atau alamat Bapak berubah, mohon diinfokan ke customer "
         "service ya, supaya reminder dan tagihan tetap sampai tepat waktu.",
         ID_SOURCE, []),
]


def main() -> None:
    out = settings.processed_dir / "market_records.jsonl"
    records = PH_RECORDS + ID_RECORDS

    # Guard against hand-authoring collisions in record ids.
    seen: dict[str, int] = {}
    for r in records:
        seen[r.record_id] = seen.get(r.record_id, 0) + 1
        if seen[r.record_id] > 1:
            r.record_id = f"{r.record_id}_{seen[r.record_id]:02d}"

    with out.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(r.model_dump_json() + "\n")

    print(f"wrote {len(records)} market records -> {out}")
    print(f"  ph: {len(PH_RECORDS)}")
    print(f"  id: {len(ID_RECORDS)}")
    from collections import Counter
    for market in ("ph", "id"):
        cats = Counter(r.category for r in records if r.market == market)
        print(f"  {market} categories: {dict(cats)}")


if __name__ == "__main__":
    main()
