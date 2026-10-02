# Q2 - Retrieval Evaluation

Auto-graded screening pass over the evaluation set in `q2_kb/eval/queries.py`. Every row includes the retrieved chunk so the verdict can be checked by reading rather than trusted.

| verdict | count |
|---|---|
| correct | 20 |
| partially_correct | 0 |
| incorrect | 0 |
| **total** | **20** |

## Results

### Q01 - What is covered under the ReAssure plan?

- **Intent:** product  |  **Expected:** answer
- **Verdict:** `correct`  |  **Confidence:** 7.753 (floor 1.200)  |  **Latency:** 2652.7 ms
- **Retrieved record:** `kb_product_plan_b2cb259c_00` - Can I include my family members under the ReAssure 2.0 plan?
- **Source:** Can I include my family members under the ReAssure 2.0 plan? - https://www.nivabupa.com/family-health-insurance-plans/health-premia.html#can-i-include-my-family-members-under-the-reassure-2-0-plan (v1.0)

  > Yes, the ReAssure 2.0 plan offers family coverage, allowing you to include your spouse, children, and dependent parents. You can select the right coverage options based on the needs of your family.

- **Relevance:** Retrieved at cosine 7.753 (floor 1.200); all required concepts present in top-4.
- **Why this query is in the set:** Named-product lookup; tests that product pages indexed cleanly.

### Q02 - Do you cover AYUSH or ayurvedic treatment?

- **Intent:** product  |  **Expected:** answer
- **Verdict:** `correct`  |  **Confidence:** 7.976 (floor 2.000)  |  **Latency:** 139.0 ms
- **Retrieved record:** `kb_eligibility_rule_3ccf95c9_00` - AYUSH Treatments Covered Up to Full Sum Insured
- **Source:** AYUSH Treatments Covered Up to Full Sum Insured - https://www.nivabupa.com/health-insurance-articles/new-irdai-rules-what-has-changed-for-policyholders.html#ayush-treatments-covered-up-to-full-sum-insured (v1.0)

  > AYUSH treatment, covering Ayurveda, Yoga, Unani, Siddha, and Homoeopathy, gets the same treatment as conventional medicine now.
  > - Sub-limits on AYUSH claims have been removed.
  > - Policyholders can claim up to their full sum insured, same as any other procedure.
  > - This benefits patients who rely on alternative treatment systems alongside or instead of allopathic care.

- **Relevance:** Retrieved at cosine 7.976 (floor 2.000); all required concepts present in top-4.
- **Why this query is in the set:** Benefit block extracted from a Mantine accordion, not prose.

### Q03 - Is maternity covered and what is the waiting period for it?

- **Intent:** product  |  **Expected:** answer
- **Verdict:** `correct`  |  **Confidence:** 7.768 (floor 1.200)  |  **Latency:** 346.9 ms
- **Retrieved record:** `kb_coverage_rule_3f7bf0df_00` - Maternity-Related Waiting Period, If Applicable
- **Source:** Maternity-Related Waiting Period, If Applicable - https://www.nivabupa.com/health-insurance/5-lakh-health-insurance-plan.html#maternity-related-waiting-period-if-applicable (v1.0)

  > Maternity cover is not part of every INR 5 lakh plan, so check the policy wording first. Where it is offered, the waiting period generally falls between 24 and 48 months, and delivery costs often carry a separate sub-limit. Couples planning a child should buy the policy well ahead of that timeline.

- **Relevance:** Retrieved at cosine 7.768 (floor 1.200); all required concepts present in top-4.
- **Why this query is in the set:** Two-part question; chunk must carry benefit AND its condition.

### Q04 - What is the waiting period for pre-existing diseases?

- **Intent:** policy  |  **Expected:** answer
- **Verdict:** `correct`  |  **Confidence:** 10.008 (floor 1.200)  |  **Latency:** 204.5 ms
- **Retrieved record:** `kb_coverage_rule_b06b319f_00` - What is the waiting period for pre-existing diseases in a ₹25 lakh health insurance plan?
- **Source:** What is the waiting period for pre-existing diseases in a ₹25 lakh health insurance plan? - https://www.nivabupa.com/health-insurance/25-lakh-health-insurance-plans.html#what-is-the-waiting-period-for-pre-existing-diseases-in-a-25 (v1.0)

  > Most insurers impose a waiting period of 2 to 3 years for pre-existing diseases. During this time, any treatment related to such conditions may not be covered. It's important to read the policy wording carefully and choose a plan with the shortest waiting period, if possible.

- **Relevance:** Retrieved at cosine 10.008 (floor 1.200); all required concepts present in top-4.
- **Why this query is in the set:** Core policy rule; the single most common qualification question.

### Q05 - PED waiting period

- **Intent:** policy  |  **Expected:** answer
- **Verdict:** `correct`  |  **Confidence:** 7.937 (floor 1.200)  |  **Latency:** 331.3 ms
- **Retrieved record:** `kb_product_plan_85027079_00` - Waiting periods
- **Source:** Waiting periods - https://www.nivabupa.com/family-health-insurance-plans/health-companion-family-floater.html#waiting-periods (v1.0)

  > •Pre-existing Diseases - Pre-existing Disease (PED) and its direct complications shall be excluded until the expiry of 24 months of continuous coverage •Specified disease/procedure - Treatment of the listed conditions shall be excluded until the expiry of 24 months of continuous coverage. •30-day waiting period - Expenses related to the treatment of any Illness within 30 days from the first Policy

- **Relevance:** Retrieved at cosine 7.937 (floor 1.200); all required concepts present in top-4.
- **Why this query is in the set:** JARGON form of Q04. Tests BM25 + canonical-term expansion. If this fails while Q04 passes, terminology normalisation is not working.

### Q06 - I already have diabetes, will my treatment be covered?

- **Intent:** policy  |  **Expected:** answer
- **Verdict:** `correct`  |  **Confidence:** 3.368 (floor 1.200)  |  **Latency:** 277.4 ms
- **Retrieved record:** `kb_coverage_rule_9d0e850e_00` - Diabetes
- **Source:** Diabetes - https://www.nivabupa.com/health-insurance/health-insurance-plans.html#diabetes (v1.0)

  > Diabetes is India's most prevalent chronic condition, affecting an estimated 77 million adults. It is also one of the most important considerations when buying health insurance.
  > If you already have diabetes when you buy a policy:
  > - It is classified as a pre-existing disease
  > - Under current IRDAI guidelines, the maximum waiting period is 36 months
  > - Diabetes-related hospitalisation 

- **Relevance:** Retrieved at cosine 3.368 (floor 1.200); all required concepts present in top-4.
- **Why this query is in the set:** PARAPHRASE form of Q04 with no shared vocabulary. Tests dense retrieval. Q04/Q05/Q06 together isolate which retriever works.

### Q07 - What is a co-payment and when does it apply?

- **Intent:** policy  |  **Expected:** answer
- **Verdict:** `correct`  |  **Confidence:** 5.392 (floor 1.200)  |  **Latency:** 627.8 ms
- **Retrieved record:** `kb_coverage_rule_64b0aba1_00` - What is co-payment in diabetes plans?
- **Source:** What is co-payment in diabetes plans? - https://www.nivabupa.com/health-insurance/medical-insurance-plans-for-diabetes.html#what-is-co-payment-in-diabetes-plans (v1.0)

  > Co-payment is a percentage of the claim amount that the policyholder must pay. Many diabetes plans include a mandatory co-pay of 10 to 20 percent to keep the premium costs manageable.

- **Relevance:** Retrieved at cosine 5.392 (floor 1.200); all required concepts present in top-4.

### Q08 - What is the room rent limit on my policy?

- **Intent:** policy  |  **Expected:** answer
- **Verdict:** `correct`  |  **Confidence:** 4.185 (floor 1.500)  |  **Latency:** 132.2 ms
- **Retrieved record:** `kb_coverage_rule_5ba0762a_00` - Sub-limit on Room Rent
- **Source:** Sub-limit on Room Rent - https://www.nivabupa.com/health-insurance/sub-limit-in-health-insurance.html#sub-limit-on-room-rent (v1.0)

  > Under the room rent sublimit alternative, your medical insurance company covers the hospital room rent per day, but only up to a specific limit. If you choose a room with a daily rent that exceeds your sub-limit, you must pay the additional charges. This type of sub-limit also applies to the type of hospital room. For instance, due to their low expense rate, the insurer's plans may only cover the 

- **Relevance:** Retrieved at cosine 4.185 (floor 1.500); all required concepts present in top-4.

### Q09 - What is the free look period?

- **Intent:** policy  |  **Expected:** answer
- **Verdict:** `correct`  |  **Confidence:** 10.268 (floor 2.000)  |  **Latency:** 201.4 ms
- **Retrieved record:** `kb_eligibility_rule_72749463_00` - Understanding The Free Look Period
- **Source:** Understanding The Free Look Period - https://www.nivabupa.com/health-insurance/free-look-period-in-health-insurance.html#understanding-the-free-look-period (v1.0)

  > The free look period is a short time given to new policyholders to review their insurance policy after receiving the policy document, not from the purchase date, but from the day it reaches you by email or post. This period is required by the Insurance Regulatory and Development Authority of India (IRDAI) to protect consumers. During this time, you can cancel the policy if you are not satisfied. U

- **Relevance:** Retrieved at cosine 10.268 (floor 2.000); all required concepts present in top-4.

### Q10 - What is the maximum age to buy a health insurance policy?

- **Intent:** qualification  |  **Expected:** answer
- **Verdict:** `correct`  |  **Confidence:** 8.436 (floor 2.000)  |  **Latency:** 401.1 ms
- **Retrieved record:** `kb_eligibility_rule_34b0eafb_00` - No Age Limit on Buying Health Insurance
- **Source:** No Age Limit on Buying Health Insurance - https://www.nivabupa.com/health-insurance-articles/new-irdai-rules-what-has-changed-for-policyholders.html#no-age-limit-on-buying-health-insurance (v1.0)

  > Age is no longer a barrier to buying a policy.
  > - Earlier, insurers set upper age limits, often 60 or 65 years.
  > - People above that age struggled to find coverage at all.
  > - This restriction has now been removed completely, opening the market to senior citizens who were previously locked out.

- **Relevance:** Retrieved at cosine 8.436 (floor 2.000); all required concepts present in top-4.
- **Why this query is in the set:** Drives the qualification branch of the Q1 call flow.

### Q11 - Can I include my parents in a family floater policy?

- **Intent:** qualification  |  **Expected:** answer
- **Verdict:** `correct`  |  **Confidence:** 10.680 (floor 1.200)  |  **Latency:** 180.3 ms
- **Retrieved record:** `kb_coverage_rule_5180c128_00` - Can I include my parents under a ₹25 lakh family floater plan?
- **Source:** Can I include my parents under a ₹25 lakh family floater plan? - https://www.nivabupa.com/health-insurance/25-lakh-health-insurance-plans.html#can-i-include-my-parents-under-a-25-lakh-family-floater-plan (v1.0)

  > Yes, many insurers allow you to include parents under a family floater plan. However, since older individuals typically require more medical care, this may increase the premium or affect claim limits for other family members. In some cases, separate individual plans for senior parents may offer better value.

- **Relevance:** Retrieved at cosine 10.680 (floor 1.200); all required concepts present in top-4.

### Q12 - What documents do I need to buy health insurance?

- **Intent:** faq  |  **Expected:** answer
- **Verdict:** `correct`  |  **Confidence:** 9.676 (floor 1.500)  |  **Latency:** 248.1 ms
- **Retrieved record:** `kb_faq_71a6608f_00` - What documents do you require to buy health insurance?
- **Source:** What documents do you require to buy health insurance? - https://www.nivabupa.com/insurance-faq/health-insurance-faq.html#what-documents-do-you-require-to-buy-health-insurance (v1.0)

  > You need to furnish a few essential documents when signing up for health insurance. Age proof This is one of the most important documents at the time of purchasing a health insurance. Keep in mind that if you are buying insurance for your family members or purchasing a family floater plan, you must submit the age proof of all members to be insured. The following documents are accepted: Passport Aa

- **Relevance:** Retrieved at cosine 9.676 (floor 1.500); all required concepts present in top-4.

### Q13 - How do I make a cashless claim?

- **Intent:** claims  |  **Expected:** answer
- **Verdict:** `correct`  |  **Confidence:** 9.273 (floor 1.200)  |  **Latency:** 281.0 ms
- **Retrieved record:** `kb_coverage_rule_094832cc_00` - How do I make a claim under an individual health insurance policy?
- **Source:** How do I make a claim under an individual health insurance policy? - https://www.nivabupa.com/health-insurance/individual-health-insurance-plans.html#how-do-i-make-a-claim-under-an-individual-health-insurance-p (v1.0)

  > You can usually make a claim in two ways: cashless or reimbursement. For cashless claims, visit a network hospital and present your health card; your insurer will settle the bill directly with the hospital, subject to policy terms. For non-network hospitals, you’ll need to pay upfront and later submit bills for reimbursement.

- **Relevance:** Retrieved at cosine 9.273 (floor 1.200); all required concepts present in top-4.

### Q14 - Why do health insurance claims get rejected?

- **Intent:** claims  |  **Expected:** answer
- **Verdict:** `correct`  |  **Confidence:** 10.522 (floor 1.500)  |  **Latency:** 266.4 ms
- **Retrieved record:** `kb_claims_process_34891a65_00` - Why do health insurance claims get rejected?
- **Source:** Why do health insurance claims get rejected? - https://www.nivabupa.com/health-insurance-articles/health-insurance-claim-rejection-reasons.html#why-do-health-insurance-claims-get-rejected (v1.0)

  > Health insurance claims may be rejected if the treatment is not covered, a waiting period is still applicable, relevant medical information was not disclosed, or the required documents are incomplete. The exact reason will depend on the policy terms and the circumstances of the claim.

- **Relevance:** Retrieved at cosine 10.522 (floor 1.500); all required concepts present in top-4.

### Q15 - Health insurance is too expensive, why should I bother?

- **Intent:** objection  |  **Expected:** answer
- **Verdict:** `correct`  |  **Confidence:** 2.994 (floor 1.200)  |  **Latency:** 725.8 ms
- **Retrieved record:** `kb_coverage_rule_b7eacaba_00` - Will the premium for 50 lakh health insurance be too expensive?
- **Source:** Will the premium for 50 lakh health insurance be too expensive? - https://www.nivabupa.com/health-insurance/50-lakh-health-insurance-plans.html#will-the-premium-for-50-lakh-health-insurance-be-too-expensi (v1.0)

  > While higher than low-sum plans, the 50 lakh insurance policy premium is still affordable considering the large coverage it offers, especially if bought early in life.

- **Relevance:** Retrieved at cosine 2.994 (floor 1.200); all required concepts present in top-4.
- **Why this query is in the set:** Objection handling must be grounded in real cost/benefit content, not improvised by the model.

### Q16 - I already have insurance from my employer, why do I need my own?

- **Intent:** objection  |  **Expected:** answer
- **Verdict:** `correct`  |  **Confidence:** 4.584 (floor 1.200)  |  **Latency:** 129.8 ms
- **Retrieved record:** `kb_coverage_rule_b73718d4_00` - Can I buy individual medical insurance if I already have employer-provided health cover?
- **Source:** Can I buy individual medical insurance if I already have employer-provided health cover? - https://www.nivabupa.com/health-insurance/individual-health-insurance-plans.html#can-i-buy-individual-medical-insurance-if-i-already-have-emp (v1.0)

  > Yes, and it is often advisable. Employer-provided group insurance is typically limited in both sum insured and duration—often ending when you leave the job. An individual policy ensures continuous and personalised coverage, regardless of employment status, and can also serve as a top-up to enhance your existing protection.

- **Relevance:** Retrieved at cosine 4.584 (floor 1.200); all required concepts present in top-4.
- **Why this query is in the set:** Classic bancassurance/corporate-cover objection.

### Q17 - What is my current policy balance and when is my next premium due?

- **Intent:** out_of_scope  |  **Expected:** refuse
- **Verdict:** `correct`  |  **Confidence:** -2.878 (floor 1.200)  |  **Latency:** 636.6 ms
- **Retrieved:** nothing above threshold (refusal path)

- **Relevance:** Correctly refused: best cross-encoder score -2.878 below 1.200 floor for category 'coverage_rule'
- **Why this query is in the set:** Account-specific. No KB can answer this; must escalate to a human rather than guess.

### Q18 - What is the capital of France?

- **Intent:** out_of_scope  |  **Expected:** refuse
- **Verdict:** `correct`  |  **Confidence:** -9.479 (floor 1.200)  |  **Latency:** 203.6 ms
- **Retrieved:** nothing above threshold (refusal path)

- **Relevance:** Correctly refused: best cross-encoder score -9.479 below 1.200 floor for category 'coverage_rule'
- **Why this query is in the set:** Pure off-domain. If the gate lets this through, the threshold is too low.

### Q19 - Can you sell me car insurance for my new Honda?

- **Intent:** out_of_scope  |  **Expected:** refuse
- **Verdict:** `correct`  |  **Confidence:** -8.927 (floor 2.000)  |  **Latency:** 99.4 ms
- **Retrieved:** nothing above threshold (refusal path)

- **Relevance:** Correctly refused: best cross-encoder score -8.927 below 2.000 floor for category 'eligibility_rule'
- **Why this query is in the set:** Adjacent domain -- harder than Q18, because 'insurance' matches lexically across the whole corpus.

### Q20 - What was Niva Bupa's net profit last quarter?

- **Intent:** out_of_scope  |  **Expected:** refuse
- **Verdict:** `correct`  |  **Confidence:** -0.681 (floor 1.500)  |  **Latency:** 319.2 ms
- **Retrieved:** nothing above threshold (refusal path)

- **Relevance:** Correctly refused: best cross-encoder score -0.681 below 1.500 floor for category 'coverage_rule'
- **Why this query is in the set:** On-brand but financial; the corpus has no financial reporting.
