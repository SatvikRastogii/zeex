# Edge cases

Every edge case named in PROMPT.md → the behaviour → the test that proves it.
Test ids are `file::test` under `backend/tests/` unless marked `e2e:`.
Filled in stage by stage; gaps are listed at the bottom with a reason.

## Money, units, IDs, audit (Stage 2)

| Case | Behaviour | Test |
|---|---|---|
| Rounding halves (incl. negative) | Round half away from zero, integer paise only | `test_money.py::test_div_round_half_up` |
| GST on paise | `apply_bp`, half up | `test_money.py::test_apply_bp_gst` |
| Indian digit grouping | `₹5,00,000.00`, `₹1,23,45,678.90` | `test_money.py::test_format_inr_indian_grouping` |
| Rupee strings from documents | `Rs. 1,00,000`, `₹380`, `380.005` parsed exactly | `test_money.py::test_rupees_to_paise` |
| Garbage amounts | `NaN`, `Infinity`, `1.2.3` rejected | `test_money.py::test_rupees_to_paise_rejects` |
| bag ↔ tonne (cement) | 1 t = 20 bags, exact fractions | `test_units.py::test_cement_bag_tonne_both_ways` |
| brass ↔ cft | 1 brass = 100 cft | `test_units.py::test_brass_cft_both_ways` |
| Per-tonne price for a per-bag item | ₹7,600/t → ₹380/bag | `test_units.py::test_per_tonne_price_restated_per_bag` |
| IST year rollover in IDs | 31 Dec 19:00 UTC → `BOM-2027-…` | `test_ids.py::test_year_segment_uses_ist` |
| 50 parallel inserts | No duplicate public codes (sequences) | `test_ids.py::test_50_parallel_inserts_get_unique_codes` |
| Audit tampering | UPDATE/DELETE on `audit_log` rejected by trigger | `test_audit_log.py::test_audit_update_and_delete_rejected` |
| Seed re-run | Identical result, no duplicates | `test_seed.py::test_seed_is_idempotent` |
| Invalid state transition | Raises, state unchanged, logged | `test_states.py::test_invalid_transition_raises_and_leaves_state` |

## Auth, roles, tenancy (Stage 3)

| Case | Behaviour | Test |
|---|---|---|
| OTP expiry | Invalid at 5:00, valid at 4:59 | `test_auth.py::test_otp_expires_after_5_minutes`, `::test_otp_valid_just_before_expiry` |
| OTP reuse | Second use rejected | `test_auth.py::test_otp_single_use` |
| Attempt limit | 5 wrong tries lock the challenge, even for the right code | `test_auth.py::test_five_wrong_attempts_lock_the_challenge` |
| Rate limit | 6th code in 15 min → 429; window slides | `test_auth.py::test_rate_limit_5_challenges_per_15_minutes` |
| Old code after re-request | Only the newest code works | `test_auth.py::test_new_code_invalidates_the_old_one` |
| Unknown phone | Same response, cannot verify | `test_auth.py::test_unknown_phone_looks_the_same_and_cannot_verify` |
| Generic errors | "Invalid or expired code" for every failure | `test_auth.py::test_wrong_code_generic_error` |
| OTP at rest | argon2 hash, never the code | `test_auth.py::test_otp_stored_hashed` |
| Session expiry | 401 at 12 h | `test_auth.py::test_session_expires_after_12_hours` |
| Logout | Copied cookie stops working | `test_auth.py::test_logout_invalidates_session_server_side` |
| Tampered token | 401 | `test_auth.py::test_tampered_token_rejected` |
| Deactivated user | Session dies, no new codes | `test_auth.py::test_deactivated_user_loses_session_and_cannot_request` |
| Each permission row | Matrix + endpoint checks | `test_permissions.py::test_permission_matrix` and endpoint tests |
| Cross-tenant ids | 404 (same as missing) on every tenant endpoint | `test_tenancy.py::test_other_tenants_rows_are_404`, `::test_missing_rows_are_also_404` |
| Vendor privacy | Vendor sees only own messages; other ids 404 | `test_tenancy.py::test_vendor_sees_only_own_messages` |
| Owner self-lockout | Cannot demote or deactivate self | `test_permissions.py::test_owner_cannot_demote_or_deactivate_self` |
| Weights | Must have all five keys, 0–100, sum 100 | `test_permissions.py::test_weights_must_sum_to_100` |

## BOM upload and validation (Stage 4)

| Case | Behaviour | Test |
|---|---|---|
| Empty file | "The file is empty." | `test_bom_rows.py::test_empty_file` |
| Header only | "no items" | `test_bom_rows.py::test_header_only` |
| Wrong columns | Lists the expected columns | `test_bom_rows.py::test_wrong_columns` |
| Header synonyms | Material/Qty/UOM/Required By accepted | `test_bom_rows.py::test_header_aliases_accepted` |
| > 500 rows | Rejected with the count; 500 accepted | `test_bom_rows.py::test_more_than_500_rows_rejected`, `::test_exactly_500_rows_ok` |
| File > 5 MB / wrong type / corrupt XLSX | 413 / 422 with message | `test_boms_api.py::test_validate_file_rejects`, `test_bom_rows.py::test_corrupt_xlsx` |
| Duplicate lines | Merged (quantities added), warning shown on both | `test_bom_rows.py::test_duplicates_merged`, `test_boms_api.py::test_duplicate_lines_merged_on_create` |
| Zero / negative / non-numeric qty | Row error | `test_bom_rows.py::test_bad_quantities` |
| Fractional bags/nos/boxes | Row error | `test_bom_rows.py::test_fractional_bags_rejected` |
| Unknown item | Closest matches (pg_trgm) suggested; builder must choose | `test_bom_rows.py::test_unknown_item_suggests_and_requires_choice`, `test_boms_api.py::test_unknown_item_gets_trigram_suggestions` |
| "2 truck" of sand | Not convertible; asks for cft or brass | `test_bom_rows.py::test_truck_of_sand_asks_for_cft_or_brass` |
| Past date / before feasible lead time | Row error naming the earliest feasible date | `test_bom_rows.py::test_bad_dates`, `::test_earliest_feasible_date_accepted` |
| Site from another org | "Unknown site" (row); site_id → 404 | `test_bom_rows.py::test_site_from_another_org_is_unknown`, `test_boms_api.py::test_site_of_another_org_is_404` |
| XLSX formulas | Saved values read; unsaved formula → "open in Excel and save" | `test_bom_rows.py::test_xlsx_values_and_dates`, `::test_xlsx_formula_without_saved_value_is_flagged` |
| Non-UTF-8 CSV | cp1252 / UTF-16 decoded | `test_bom_rows.py::test_non_utf8_csv_read_as_cp1252`, `::test_utf16_csv` |
| Nothing published until valid | Create refuses any row error; nothing stored | `test_boms_api.py::test_create_with_errors_creates_nothing` |
| Double submit | Same `client_ref` returns the same BOM | `test_boms_api.py::test_create_is_idempotent` |
| Editing a published BOM | Revision +1; invited RFQ marked stale | `test_boms_api.py::test_edit_published_line_bumps_revision_and_marks_invited_rfq_stale` |
| Editing after approval stage | 409 | `test_boms_api.py::test_edit_locked_after_approval_stage` |
| Another org's uploaded file | Rejected | `test_boms_api.py::test_file_ref_of_another_org_rejected` |

## Vendor matching (Stage 5)

| Case | Behaviour | Test |
|---|---|---|
| Zero matches | RFQ `no_vendors_matched`, suggestions shown | `test_rfqs_api.py::test_all_blocked_gives_no_vendors_matched`, `test_matching.py::test_zero_matches_warns_with_suggestions` |
| One match | Warning "no competition on price" + suggestions | `test_rfqs_api.py::test_opted_out_vendor_filtered_and_single_match_warned`, `test_matching.py::test_one_match_warns` |
| All matches blocked or opted out | Filtered with reason; zero-match path | `test_rfqs_api.py::test_all_blocked_gives_no_vendors_matched` |
| Blocked by one builder only | Excluded for that builder, matched for others | `test_rfqs_api.py::test_blocked_by_this_builder_only` |
| Linked to builder A, not B | Invisible to B (not even listed as excluded) | `test_rfqs_api.py::test_vendor_linked_to_a_but_not_b`, `test_matching.py::test_unlinked_vendor_is_invisible` |
| At capacity for the week | Excluded: "No capacity left in the needed-by week" | `test_rfqs_api.py::test_vendor_at_capacity_for_the_week` |
| Missing GSTIN | Excluded with reason | `test_rfqs_api.py::test_missing_gstin_filtered` |
| Opted out | Excluded with reason | `test_rfqs_api.py::test_opted_out_vendor_filtered_and_single_match_warned` |
| Ties in score | Distance, then name; repeat runs identical | `test_matching.py::test_ties_broken_by_distance_then_name`, `test_rfqs_api.py::test_same_inputs_same_order` |
| Widen radius | Re-run brings the vendor in | `test_rfqs_api.py::test_widen_radius_rematch` |
| Adding an ineligible vendor | 422 with the filter reason | `test_rfqs_api.py::test_cannot_add_ineligible_vendor` |
| Shortlist after RFQs sent | 409 | `test_rfqs_api.py::test_shortlist_locked_after_invites` |
| Site engineer edits shortlist | 403 (can still view) | `test_rfqs_api.py::test_shortlist_edit_permission`, `::test_site_engineer_can_view_rfq` |

## Gaps

| Case | Reason |
|---|---|
| Stale RFQ → vendors re-invited with the change | Marking stale is done (Stage 4); re-sending needs the outreach agent (Stage 6). |
