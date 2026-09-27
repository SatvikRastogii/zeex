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

## Messaging, outreach, jobs, demo clock (Stage 6)

| Case | Behaviour | Test |
|---|---|---|
| Duplicate inbound message | Stored once; second call reports duplicate | `test_channel.py::test_duplicate_inbound_is_stored_once`, `test_outreach.py::test_vendor_inbox_conversations_and_reply` |
| Out-of-order delivery | Ordered by device time; future device time capped | `test_channel.py::test_out_of_order_arrival_keeps_device_time`, `::test_device_time_in_future_is_capped` |
| Message to opted-out vendor | Blocked, audited, not delivered, not in inbox | `test_channel.py::test_opted_out_vendor_is_blocked_and_logged`, `test_outreach.py::test_opt_out_after_queueing_blocks_and_logs`, `::test_blocked_messages_are_not_in_the_vendor_inbox` |
| Opted-out vendor on the shortlist | Skipped at send | `test_outreach.py::test_opted_out_on_shortlist_is_skipped` |
| STOP | Immediate, global, confirmation sent; START re-opts in; "stop" inside a sentence ignored | `test_channel.py::test_stop_opts_out_globally`, `::test_start_opts_back_in`, `::test_stop_inside_a_sentence_is_not_opt_out` |
| Free text outside 24 h window | Refused (template required) | `test_channel.py::test_free_text_needs_open_window` |
| Job crash mid-run | Rolled back, retried with backoff, then succeeds | `test_jobs.py::test_crash_is_retried_with_backoff_then_succeeds` |
| Job keeps failing | Marked failed after 5 attempts | `test_jobs.py::test_gives_up_after_max_attempts` |
| Worker restart | Stale `running` jobs recovered after 5 min | `test_jobs.py::test_stale_running_job_recovered_after_worker_restart` |
| Two workers | SKIP LOCKED claims are disjoint | `test_jobs.py::test_skip_locked_claims_are_disjoint` |
| Per-thread ordering | FIFO per key, even across retries | `test_jobs.py::test_ordering_key_is_fifo_even_across_retries` |
| Clock jumps past several deadlines | All fire, in order | `test_jobs.py::test_clock_jump_fires_all_due_jobs_in_order`, `test_outreach.py::test_clock_jump_past_every_deadline_fires_in_order` |
| Outside working hours | Deferred to 09:00 IST | `test_outreach.py::test_outside_working_hours_deferred_to_next_morning`, `test_working_hours.py` |
| Reminder | 50% of window, non-responders only, once, never at the close | `test_outreach.py::test_reminder_at_half_window_only_to_non_responders`, `test_working_hours.py::test_reminder_never_lands_on_the_close` |
| More than 15 vendors | 15 invited, rest `skipped_limit` | `test_outreach.py::test_max_15_invites` |
| Exact address before award | Never in invites (area only) | `test_outreach.py::test_send_rfqs_in_working_hours` |
| BOM edited after invites | Vendors get `rfq_update` with the change | `test_outreach.py::test_stale_rfq_resent_with_the_change` |
| Reply on an RFQ the vendor was not invited to | 404 | `test_outreach.py::test_vendor_cannot_reply_on_rfq_they_were_not_invited_to` |

## Quotation intake and parsing (Stage 7)

| Case | Behaviour | Test |
|---|---|---|
| Arithmetic mismatch | Flagged, vendor asked to check, confirmation required | `test_quotes.py::test_arithmetic_mismatch_flagged_and_vendor_told` |
| Per-tonne quote for a per-bag RFQ | Converted and shown (₹7,600/t = ₹380/bag) | `test_quotes.py::test_per_tonne_quote_converted_to_per_bag` |
| GST included vs excluded | Recorded as quoted with the rate | `test_quotes.py::test_gst_included_vs_excluded`, `test_quote_text.py::test_gst_phrases` |
| Missing validity | Today + 7 days, flagged | `test_quotes.py::test_missing_validity_defaults_and_flags` |
| Validity too short | Flagged | `test_quotes.py::test_short_validity_flagged` |
| Quote after window closed | Recorded as rejected/late, not ranked, vendor told politely | `test_quotes.py::test_quote_after_window_closed_is_recorded_not_ranked` |
| Vendor quotes twice | Latest confirmed wins, earlier superseded, history kept | `test_quotes.py::test_second_quote_supersedes_first_and_history_is_kept` |
| Ambiguous RFQ for a document | List picker; parsed against the chosen RFQ | `test_quotes.py::test_ambiguous_rfq_asks_which_one`, `::test_single_open_rfq_needs_no_question` |
| Hidden-instruction PDF | Parsed as data (visible rate), flagged, nothing auto-accepted, RFQ unchanged | `test_quotes.py::test_hidden_instructions_are_just_data` |
| Outlier price | Flagged against the reference median; confirmation required even for form quotes | `test_quotes.py::test_outlier_price_needs_confirmation` |
| Unreadable file | Vendor asked to resend; no quote | `test_quotes.py::test_unreadable_photo_asks_to_resend`, `::test_corrupt_pdf_is_unreadable` |
| File too large / wrong type | Vendor told; no parse | `test_quotes.py::test_file_too_large`, `::test_wrong_file_type` |
| Rate list | Only the RFQ item's row is used | `test_quotes.py::test_rate_list_keeps_only_the_rfq_item` |
| Scanned PDF / photo | Sent to the model as a file (multimodal) | `test_quotes.py::test_scanned_documents_go_multimodal` |
| Non-form quote | Awaits vendor confirmation (Yes/Edit) | `test_quotes.py::test_text_quote_needs_confirmation`, `::test_edit_button_reopens_the_quote` |
| Withdrawal | Latest confirmed quote withdrawn | `test_quotes.py::test_withdraw` |
| LLM returns invalid JSON / extra fields | One retry, then deterministic fallback | `test_quotes.py::test_invalid_llm_json_retries_then_falls_back`, `::test_llm_extra_fields_rejected`, `test_quote_text.py::test_structured_retries_once_then_gives_up` |
| Target/max price privacy | Never in any prompt | `test_quotes.py::test_target_and_max_price_never_reach_the_llm` |
| Quantities mistaken for prices | "30 bags" / "50 kg" are not rates | `test_quote_text.py::test_quantities_are_not_prices` |
| Double-submitted form / re-run parse job | One quote | `test_quotes.py::test_form_submission_is_idempotent`, `::test_every_parse_is_idempotent` |
| Quote on an RFQ not invited to; another org's quotes | 404 | `test_quotes.py::test_vendor_cannot_quote_on_rfq_not_invited`, `::test_quotes_cross_tenant_404` |

## Evaluation, shortlist and big orders (Stage 8)

| Case | Behaviour | Test |
|---|---|---|
| ₹360 + ₹25 freight vs ₹375 delivered | ₹385 vs ₹375 landed; delivered wins | `test_evaluation.py::test_freight_quote_vs_delivered_quote` |
| GST mode incl/excl | GST added or removed only as needed | `test_evaluation.py::test_gst_added_only_when_comparing_incl_and_quote_excludes` |
| Rounding | Exact until one final half-up rounding | `test_evaluation.py::test_rounding_happens_once_at_the_end` |
| Weights must sum to 100 | Refused (API 422; engine assertion) | `test_evaluation.py::test_weights_must_sum_to_100`, `test_permissions.py::test_weights_must_sum_to_100` |
| Disqualification reasons | Needed-by, no date, expired/short validity, part qty without partial, unit, wrong item | `test_evaluation.py::test_disqualification_reasons`, `test_evaluation_api.py::test_bid_close_scores_and_recommends` |
| Tie-breaks | Landed, delivery, rating, quote time | `test_evaluation.py::test_tie_breaks_landed_then_delivery_then_rating_then_time` |
| L1 differs from lowest price | Both marked | `test_evaluation.py::test_l1_by_score_can_differ_from_lowest_price` |
| Above max price | Flagged, not hidden, not L1 without override | `test_evaluation.py::test_above_max_is_flagged_not_hidden`, `test_evaluation_api.py::test_max_price_flags_and_excludes_from_l1` |
| Split award across capacity-limited vendors | Allocated by score within capacity; covers full qty | `test_evaluation.py::test_split_across_capacity_limited_vendors`, `test_evaluation_api.py::test_big_order_split_award` |
| Minimum order in split | Vendor skipped with reason | `test_evaluation.py::test_split_skips_vendor_below_minimum_order` |
| Shortfall | Reported with options, never silently short | `test_evaluation.py::test_shortfall_reported_with_options`, `test_evaluation_api.py::test_big_order_shortfall` |
| Fewer than 2 quotes | Window extended once, builder notified, vendors reminded | `test_evaluation_api.py::test_one_quote_extends_window_once_then_goes_to_builder_without_negotiation` |
| Single quote after extension | No negotiation implying competition; straight to approval | same test |
| No quotes after extension | `insufficient_quotes` | `test_evaluation_api.py::test_no_quotes_after_extension_is_insufficient` |
| Hidden-instruction quote | Ranking unaffected | `test_evaluation_api.py::test_hidden_instruction_quote_does_not_change_ranking` |
| Weight change | Re-score changes the order | `test_evaluation_api.py::test_weight_change_rescores` |
| Clock jump replay | Each job runs at its own time | `test_jobs.py::test_catch_up_runs_each_job_at_its_own_time` |
| Unloading charge | Spread over the quantity in landed cost | `test_evaluation.py::test_unloading_spread_over_quantity` |

## Negotiation (Stage 9)

| Case | Behaviour | Test |
|---|---|---|
| Cooperative reaches target early | Stops after the round that reaches it | `test_negotiation.py::test_cooperative_reaches_target_and_stops_early` |
| Stubborn | Ends after round 3 (best and final) | `test_negotiation.py::test_stubborn_ends_after_round_three` |
| Vague | Clarify once, then `needs_human` | `test_negotiation.py::test_vague_twice_goes_to_a_human` |
| Injection attempt | No effect on price or state | `test_negotiation.py::test_injection_has_no_effect_on_price_or_state`, `test_reply_text.py::test_injection_is_not_an_acceptance` |
| Term change | `needs_human`, re-score, never auto-accepted | `test_negotiation.py::test_term_change_rescores_and_never_auto_accepts` |
| Wants a call | `needs_human` | `test_negotiation.py::test_caller_goes_to_a_human` |
| Timeout | Nudge, then `timed_out` | `test_negotiation.py::test_slow_vendor_nudged_then_timed_out` |
| "okay/done" | Best-and-final at our ask, not a deal | `test_negotiation.py::test_ok_is_best_and_final_not_a_deal` |
| Offer above max | Not recommended | `test_negotiation.py::test_offer_above_max_is_not_recommended` |
| LLM invalid JSON | Retry, then deterministic reader; twice → human | `test_negotiation.py::test_invalid_reply_json_retries_then_falls_back`, `::test_two_parse_failures_go_to_a_human` |
| LLM writes a wrong number | Validator rejects; template used | `test_negotiation.py::test_writer_wrong_number_caught_by_validator` |
| Builder takes over | Agent sends nothing more (no nudges either) | `test_negotiation.py::test_builder_take_over_stops_the_agent`, `::test_manual_message_requires_take_over` |
| Deadline mid-round | All threads closed, RFQ to approval | `test_negotiation.py::test_deadline_mid_round_closes_everything` |
| Burst replies within 45 s | One parse | `test_negotiation.py::test_burst_replies_merged_into_one_parse` |
| Stale reply | Recorded, ignored | `test_negotiation.py::test_stale_reply_recorded_but_ignored` |
| Competitor disclosure | "Lower offer" first, exact price on repeat, never names; benchmark vendor not misled | `test_negotiation.py::test_disclosure_lower_offer_first_then_price`, `::test_benchmark_vendor_is_not_told_of_a_lower_offer` |
| Target/max/other names in prompts | Never | `test_negotiation.py::test_target_and_max_never_in_any_prompt` |
| Pricing: floor, 3%/2%/final, match benchmark | As specified | `test_pricing.py` |
| Take-over permissions and tenancy | Site engineer 403; other org 404 | `test_negotiation.py::test_take_over_permissions_and_tenancy` |
| Automation disclosure | First message on every thread: "Automated assistant for {builder}." | `test_negotiation.py::test_negotiation_starts_with_disclosed_automation` |

## Approval, work orders and conflicts (Stage 10)

| Case | Behaviour | Test |
|---|---|---|
| Two users approve at the same time | One wins; the other gets "Already approved by X at HH:MM" | `test_approvals.py::test_two_users_approve_at_the_same_time`, `::test_second_approver_sees_who_approved` |
| Double-click approve | Idempotent: same POs, nothing new | `test_approvals.py::test_double_click_is_idempotent` |
| Approval after an offer expired | Blocked; reconfirm option | `test_approvals.py::test_expired_offer_blocks_approval_and_can_be_reconfirmed` |
| Above the approver's limit | Routed to the owner, not rejected | `test_approvals.py::test_pm_above_limit_is_routed_to_owner`, `::test_pm_within_limit_approves` |
| Site engineer approves | 403 | `test_approvals.py::test_site_engineer_cannot_approve` |
| Above max price | Needs explicit override | `test_approvals.py::test_above_max_needs_override` |
| Capacity conflict across builders | Second award blocked; runner-up offered and approved | `test_approvals.py::test_capacity_conflict_across_builders` |
| Vendor confirms | `vendor_confirmed` | `test_approvals.py::test_vendor_confirms_po` |
| Vendor declines after award | Capacity released; runner-up offered (one tap) | `test_approvals.py::test_vendor_declines_runner_up_offered` |
| Vendor never confirms | PO expires; runner-up offered | `test_approvals.py::test_unconfirmed_po_expires_and_runner_up_offered` |
| Runner-up also expired | Approval blocked; re-open bidding without the failed vendor | `test_approvals.py::test_runner_up_also_expired_then_rebid` |
| Split award | Several POs covering the full quantity | `test_approvals.py::test_split_award_issues_several_pos` |
| Cancel BOM during negotiation | Threads closed, vendors notified, no POs, no nudges | `test_approvals.py::test_cancel_bom_during_negotiation` |
| Cancel after PO issued | PO cancelled, vendor notified, capacity released | `test_approvals.py::test_cancel_after_po_issued_releases_capacity`, `::test_cancel_single_po` |
| Exact address before award | Only in the winner's PO message | `test_approvals.py::test_approve_issues_po_to_winner_and_tells_the_others` |
| Other org's POs | 404 | `test_approvals.py::test_cross_tenant` |
| Losing vendors | Polite `not_selected`; exact address only to the winner | `test_approvals.py::test_approve_issues_po_to_winner_and_tells_the_others` |

## Delivery, invoices, ratings and closure (Stage 11)

| Case | Behaviour | Test |
|---|---|---|
| Short delivery | Flagged; PO stays open; close needs a shortfall note; quantity accuracy drops | `test_deliveries.py::test_short_delivery_flagged_and_closed_with_note` |
| Over-delivery | Flagged; only the ordered quantity accepted | `test_deliveries.py::test_over_delivery_accepts_only_ordered_quantity` |
| Receipt before dispatch | Blocked | `test_deliveries.py::test_receipt_before_dispatch_blocked` |
| Invoice price differs from PO | Flagged; no auto-accept; accepted only by owner/PM with a reason | `test_deliveries.py::test_invoice_price_mismatch_needs_a_person` |
| Invoice for a different PO | Flagged | `test_deliveries.py::test_invoice_for_a_different_po` |
| Delivery after needed-by | `late_days` flag; on-time rating drops | `test_deliveries.py::test_late_delivery_lowers_on_time_rating` |
| Partial deliveries | Only when the PO allows; remaining tracked | `test_deliveries.py::test_partial_dispatch_needs_partial_allowed` |
| Closure | Price history appended; ratings updated; RFQ and BOM closed | `test_deliveries.py::test_full_lifecycle_to_closure` |
| Dispatch twice (retry) / unconfirmed PO / other vendor | Idempotent / 409 / 404 | `test_deliveries.py::test_dispatch_is_idempotent`, `::test_unconfirmed_po_cannot_be_dispatched`, `::test_other_vendor_cannot_dispatch` |
| Receipt photo | Required, must be an image | `test_deliveries.py::test_photo_is_required_and_must_be_an_image` |
| Site engineer | Can confirm delivery, cannot close | `test_deliveries.py::test_site_engineer_receives_but_cannot_close` |
| Vendor directory / audit scope | Linked vendors only; own org's audit only | `test_directory.py` |

## Demo and end-to-end (Stages 12–13)

| Case | Behaviour | Test |
|---|---|---|
| `make reset && make demo` | Every scenario reaches its demo point through the real API, on seeded vendor history | `test_demo.py::test_every_scenario_reaches_its_demo_point` |
| Two builders, one vendor's capacity | Second approval refused, capacity untouched | `test_demo.py::test_capacity_conflict_blocks_second_builder` |
| Full run | Scenario 1 from publish to a closed order with the demo clock | `test_demo.py::test_play_runs_scenario_one_to_a_closed_order` |
| Persona auto-reply | Vendors answer by themselves; slow never answers, vague never quotes | `test_demo.py::test_personas_reply_by_themselves` |
| Control panel | Admin only; persona and mode settings validated | `test_demo.py::test_demo_panel_settings_and_personas` |
| Happy path in the browser | Sign in with OTP → approve L1 → work order listed | `e2e: journeys.spec.ts › happy path` |
| Big order in the browser | Approve split → one work order per vendor | `e2e: journeys.spec.ts › big order` |
| Capacity conflict in the browser | Refusal shown → approve another vendor | `e2e: journeys.spec.ts › capacity conflict` |
| Hidden text in a PDF | Flagged, read at the printed ₹400, RFQ still bidding, nothing recommended | `e2e: journeys.spec.ts › PDF with hidden instructions` |
| No network in tests | LLM is the mock, WhatsApp is simulated; no test calls an external API | whole suite (`conftest.py` forces `LLM_PROVIDER=mock` and clears the Gemini key) |

## Gaps

| Case | Reason |
|---|---|
| Live Gemini parsing | No API key available in this environment; the provider is implemented against the documented REST API but only the mock is exercised by tests. |
| Brand-specific BOM lines | BOM lines have no brand field; grade is enforced through the catalog item. Add a brand column if clients need brand-locked lines. |
| Real WhatsApp Cloud API (webhook signature, media expiry, Meta templates) | Stage 14 not chosen; the channel is simulated behind `MessageChannel`. |
| AWS (Step Functions, SQS, S3, SES) | Stage 15 not chosen; see `docs/ARCHITECTURE.md` for the mapping. |
| Dispatch vehicle number | Stored and audited, but no test asserts its formatting (upper-case, 20 chars); low risk. |
| Playwright on a clean database | The journeys reset and reload the dev database in global setup, so they are not run in CI against `procure_test`. |
