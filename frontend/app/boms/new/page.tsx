"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { newRef } from "@/lib/format";
import Guard from "../../guard";

type Site = { id: string; name: string; area: string };
type Item = { id: string; code: string; name: string; canonical_unit: string };
type Input = {
  item: string;
  spec: string;
  quantity: string;
  unit: string;
  needed_by: string;
  site: string;
  partial_allowed: string;
  notes: string;
  catalog_item_id?: string | null;
};
type RowOut = {
  line_no: number;
  input: Input;
  item: Item | null;
  suggestions: Item[];
  qty_canonical_milli: number | null;
  errors: { field: string; message: string }[];
  warnings: string[];
  merged_into: number | null;
};
type Validation = { file_ref: string | null; rows: RowOut[]; ok: boolean; error_count: number };

const EMPTY: Input = { item: "", spec: "", quantity: "", unit: "", needed_by: "", site: "", partial_allowed: "", notes: "" };
const COLS: [keyof Input, string, number][] = [
  ["item", "Item", 16],
  ["spec", "Grade/spec", 8],
  ["quantity", "Qty", 6],
  ["unit", "Unit", 6],
  ["needed_by", "Needed by", 10],
  ["partial_allowed", "Partial ok", 4],
  ["notes", "Notes", 12],
];

function NewBom() {
  const router = useRouter();
  const [sites, setSites] = useState<Site[]>([]);
  const [catalog, setCatalog] = useState<Item[]>([]);
  const [siteId, setSiteId] = useState("");
  const [title, setTitle] = useState("");
  const [rows, setRows] = useState<Input[]>([{ ...EMPTY }]);
  const [result, setResult] = useState<Validation | null>(null);
  const [fileRef, setFileRef] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [clientRef] = useState(newRef);

  useEffect(() => {
    api<Site[]>("/sites").then((s) => {
      setSites(s);
      if (s.length) setSiteId(s[0].id);
    });
    api<Item[]>("/catalog").then(setCatalog);
  }, []);

  function applyResult(v: Validation) {
    setResult(v);
    setRows(v.rows.map((r) => ({ ...r.input, catalog_item_id: r.input.catalog_item_id ?? null })));
  }

  async function upload(file: File) {
    setError(null);
    setBusy(true);
    try {
      const v = await api<Validation>(`/boms/validate-file?site_id=${siteId}&filename=${encodeURIComponent(file.name)}`, {
        body: file,
      });
      setFileRef(v.file_ref);
      applyResult(v);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function check() {
    setError(null);
    setBusy(true);
    try {
      applyResult(await api<Validation>("/boms/validate", { json: { site_id: siteId, rows } }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Check failed");
    } finally {
      setBusy(false);
    }
  }

  async function create() {
    setError(null);
    setBusy(true);
    try {
      const bom = await api<{ id: string }>("/boms", {
        json: { site_id: siteId, title: title || null, rows, client_ref: clientRef, source: fileRef ? "upload" : "manual", file_ref: fileRef },
      });
      router.push(`/boms/${bom.id}`);
    } catch (e) {
      if (e instanceof ApiError && e.detail && typeof e.detail === "object" && "rows" in e.detail) {
        applyResult(e.detail as Validation);
      }
      setError(e instanceof ApiError ? e.message : "Could not create the BOM");
    } finally {
      setBusy(false);
    }
  }

  function edit(i: number, field: keyof Input, value: string) {
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [field]: value } : r)));
    setResult(null);
  }

  const rowResult = (i: number) => result?.rows[i];
  const errorFor = (i: number, f: string) => rowResult(i)?.errors.find((e) => e.field === f)?.message;

  return (
    <>
      <h1>New BOM</h1>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="box">
        <label htmlFor="site">Site</label>
        <select id="site" value={siteId} onChange={(e) => setSiteId(e.target.value)}>
          {sites.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <label htmlFor="title">Title (optional)</label>
        <input id="title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
      </div>

      <div className="box">
        <h2>Upload a file</h2>
        <p className="small">
          CSV or XLSX, up to 500 rows. Templates: <a href="/api/boms/template.csv">CSV</a> · <a href="/api/boms/template.xlsx">XLSX</a>
        </p>
        <label htmlFor="file">BOM file</label>
        <input
          id="file"
          type="file"
          accept=".csv,.xlsx"
          disabled={!siteId || busy}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void upload(f);
          }}
        />
        <p className="small muted">Or type the rows below.</p>
      </div>

      <h2>Lines</h2>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th className="num">#</th>
              {COLS.map(([k, label]) => (
                <th key={k}>{label}</th>
              ))}
              <th>Check</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const res = rowResult(i);
              return (
                <tr key={i}>
                  <td className="num">{i + 1}</td>
                  {COLS.map(([k, label, size]) => (
                    <td key={k}>
                      <input
                        aria-label={`${label} line ${i + 1}`}
                        size={size}
                        value={(r[k] as string) ?? ""}
                        onChange={(e) => edit(i, k, e.target.value)}
                        aria-invalid={Boolean(errorFor(i, k))}
                      />
                      {errorFor(i, k) && <div className="small">{errorFor(i, k)}</div>}
                      {k === "item" && ((res && !res.item) || r.catalog_item_id) && (
                        <div>
                          <select
                            aria-label={`Choose catalog item for line ${i + 1}`}
                            value={r.catalog_item_id ?? ""}
                            onChange={(e) => {
                              setRows((rs) => rs.map((x, j) => (j === i ? { ...x, catalog_item_id: e.target.value || null } : x)));
                              setResult(null);
                            }}
                          >
                            <option value="">Choose item...</option>
                            {(res?.suggestions ?? []).map((s) => (
                              <option key={`s-${s.id}`} value={s.id}>
                                Suggested: {s.name}
                              </option>
                            ))}
                            {catalog.map((c) => (
                              <option key={c.id} value={c.id}>
                                {c.name}
                              </option>
                            ))}
                          </select>
                        </div>
                      )}
                      {k === "item" && res?.item && !r.catalog_item_id && <div className="small muted">{res.item.name}</div>}
                    </td>
                  ))}
                  <td className="small">
                    {!res ? (
                      <span className="muted">not checked</span>
                    ) : res.errors.length ? (
                      <span className="status">[ERROR]</span>
                    ) : (
                      <span className="status">[OK]</span>
                    )}
                    {res?.warnings.map((w) => (
                      <div key={w}>{w}</div>
                    ))}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => {
                        setRows((rs) => rs.filter((_, j) => j !== i));
                        setResult(null);
                      }}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p>
        <button type="button" className="secondary" onClick={() => setRows((rs) => [...rs, { ...EMPTY }])}>
          Add line
        </button>{" "}
        <button type="button" className="secondary" onClick={check} disabled={busy || !siteId || rows.length === 0}>
          Check rows
        </button>{" "}
        <button type="button" onClick={create} disabled={busy || !result?.ok}>
          Create BOM
        </button>
      </p>
      {result && !result.ok && <p className="small">{result.error_count} problem(s). Fix or remove those rows, then check again.</p>}
      {!result && <p className="small muted">Check the rows before creating the BOM.</p>}
    </>
  );
}

export default function NewBomPage() {
  return (
    <Guard need="builder">
      <NewBom />
    </Guard>
  );
}
