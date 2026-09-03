import { GitBranch, X } from 'lucide-react';
import type { EntityTaxLedgerRecord, EntityVatLineageComponent } from '../types';
import { componentTypeLabel, fieldLabel, scopeLabel } from './uiLocalization';

interface EntityVatLineageDrawerProps {
  open: boolean;
  record: EntityTaxLedgerRecord | null;
  onClose: () => void;
}

function displayId(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : String(value);
}

function groupByComponentType(
  components: EntityVatLineageComponent[],
): Array<[string, EntityVatLineageComponent[]]> {
  const groups = new Map<string, EntityVatLineageComponent[]>();
  for (const component of components) {
    const existing = groups.get(component.componentType);
    if (existing) existing.push(component);
    else groups.set(component.componentType, [component]);
  }
  return [...groups.entries()];
}

const ID_FIELDS = [
  'outputVatEventId',
  'inputVatClaimId',
  'taxPrepaymentFactId',
  'priorLedgerId',
  'openingBalanceSeedId',
  'invoiceFactId',
  'sourceDocumentId',
] as const;

export function EntityVatLineageDrawer({ open, record, onClose }: EntityVatLineageDrawerProps) {
  if (!open) return null;

  const components = record?.lineageComponents ?? [];
  const groups = groupByComponentType(components);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50" aria-hidden={false}>
      <section role="dialog" aria-modal="true" aria-labelledby="entity-vat-lineage-title" className="h-full w-full max-w-2xl overflow-y-auto border-l border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl">
        <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-5 py-4">
          <div>
            <div className="flex items-center gap-2 text-[var(--color-brand)]"><GitBranch className="h-4 w-4" /><h2 id="entity-vat-lineage-title" className="text-[18px] font-bold text-[var(--color-text-primary)]">法定增值税血缘溯源</h2></div>
            <p className="mt-1 text-[12px] text-[var(--color-text-muted)]">{record ? `${record.entityName} · ${record.period} · ${scopeLabel(record.scope)}` : '未选择法人台账行'}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭血缘溯源" className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] p-2 text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-brand)] hover:text-[var(--color-brand)]"><X className="h-4 w-4" /></button>
        </div>

        <div className="space-y-4 p-5">
          {components.length === 0 ? (
            <div className="surface-card rounded-xl p-5 text-[13px] text-[var(--color-text-secondary)]" role="status">当前法定增值税台账行暂无结构化血缘组件。</div>
          ) : groups.map(([componentType, groupedComponents]) => (
            <section key={componentType} className="space-y-3" aria-label={`组件类型 ${componentTypeLabel(componentType)}`}>
              <div className="flex items-center justify-between gap-3"><h3 className="text-[14px] font-semibold text-[var(--color-brand)]">{componentTypeLabel(componentType)}</h3><span className="text-[11px] text-[var(--color-text-muted)]">{groupedComponents.length} 个组件</span></div>

              {groupedComponents.map((component, index) => (
                <article key={`${componentType}-${index}`} className="surface-card rounded-xl p-4">
                  <dl className="grid grid-cols-1 gap-x-5 gap-y-3 sm:grid-cols-2">
                    <div><dt className="text-[11px] text-[var(--color-text-muted)]">{fieldLabel('componentType')}</dt><dd className="mt-1 break-all text-[13px] font-medium text-[var(--color-text-primary)]">{componentTypeLabel(component.componentType)}</dd></div>
                    <div><dt className="text-[11px] text-[var(--color-text-muted)]">{fieldLabel('amount')}</dt><dd className="mt-1 text-[13px] font-mono-num text-[var(--color-text-primary)]">{component.amount.toLocaleString('zh-CN')}</dd></div>
                    {ID_FIELDS.map(field => (
                      <div key={field}><dt className="text-[11px] text-[var(--color-text-muted)]">{fieldLabel(field)}</dt><dd className="mt-1 text-[13px] font-mono-num text-[var(--color-text-secondary)]">{displayId(component[field])}</dd></div>
                    ))}
                  </dl>
                </article>
              ))}
            </section>
          ))}
        </div>
      </section>
    </div>
  );
}
