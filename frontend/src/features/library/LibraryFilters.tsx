import { Filter, Search } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Field, Input, Select } from "@/components/ui/Form";
import type { LibraryRecordFilters } from "@/lib/api";

import { libraryStatusLabels } from "./libraryPresentation";

interface LibraryFiltersProps {
  filters: LibraryRecordFilters;
  themes: string[];
  sources: string[];
  onChange: (update: (previous: LibraryRecordFilters) => LibraryRecordFilters) => void;
  onSubmit: () => void;
}

export function LibraryFilters({
  filters,
  themes,
  sources,
  onChange,
  onSubmit,
}: LibraryFiltersProps) {
  const toggleStatus = (status: string) => {
    onChange((previous) => ({
      ...previous,
      statuses: previous.statuses.includes(status)
        ? previous.statuses.filter((value) => value !== status)
        : [...previous.statuses, status],
    }));
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Filter aria-hidden="true" className="size-4 text-forest-600" />
          <h2 className="font-bold text-slate-900">Filtres documentaires</h2>
        </div>
      </CardHeader>
      <CardBody>
        <form
          className="grid gap-4 lg:grid-cols-4"
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit();
          }}
        >
          <Field className="lg:col-span-2" label="Titre, auteur, DOI ou métadonnée">
            <div className="relative">
              <Search
                aria-hidden="true"
                className="pointer-events-none absolute left-3.5 top-3.5 size-4 text-slate-400"
              />
              <Input
                className="pl-10"
                onChange={(event) =>
                  onChange((previous) => ({ ...previous, query: event.target.value }))
                }
                placeholder="Ex. Pascal Poupard, polyphénols, 2011"
                value={filters.query}
              />
            </div>
          </Field>
          <Field label="Thème">
            <Select
              onChange={(event) =>
                onChange((previous) => ({ ...previous, theme: event.target.value }))
              }
              value={filters.theme}
            >
              <option value="">Tous les thèmes</option>
              {themes.map((theme) => (
                <option key={theme}>{theme}</option>
              ))}
            </Select>
          </Field>
          <Field label="Source">
            <Select
              onChange={(event) =>
                onChange((previous) => ({ ...previous, source: event.target.value }))
              }
              value={filters.source}
            >
              <option value="">Toutes les sources</option>
              {sources.map((source) => (
                <option key={source}>{source}</option>
              ))}
            </Select>
          </Field>
          <fieldset className="lg:col-span-2">
            <legend className="mb-2 text-sm font-medium text-slate-700">
              Statut de pertinence
            </legend>
            <div className="flex flex-wrap gap-2">
              {Object.entries(libraryStatusLabels).map(([status, label]) => {
                const active = filters.statuses.includes(status);
                return (
                  <button
                    aria-pressed={active}
                    className={
                      active
                        ? "min-h-11 rounded-full bg-forest-600 px-3 py-2 text-xs font-bold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500"
                        : "min-h-11 rounded-full border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-500 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-500"
                    }
                    key={status}
                    onClick={() => toggleStatus(status)}
                    type="button"
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </fieldset>
          <Field label="Disponibilité">
            <Select
              onChange={(event) =>
                onChange((previous) => ({
                  ...previous,
                  abstract: event.target.value as LibraryRecordFilters["abstract"],
                }))
              }
              value={filters.abstract}
            >
              <option value="all">Tous les documents</option>
              <option value="with">Avec abstract</option>
              <option value="without">Sans abstract</option>
            </Select>
          </Field>
          <div className="flex items-end">
            <Button className="w-full" type="submit">
              Appliquer les filtres
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}
