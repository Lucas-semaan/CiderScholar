import { Filter, Search } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Field, Input, Select } from "@/components/ui/Form";
import type { LibraryRecordFilters } from "@/lib/api";

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
          <Field className="lg:col-span-2" label="Mot-clé, titre, auteur ou DOI">
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
                placeholder="Ex. polyphénols, Pascal Poupard, 2011"
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
          <Field label="Type de contenu">
            <Select
              onChange={(event) =>
                onChange((previous) => ({
                  ...previous,
                  availability: event.target.value as LibraryRecordFilters["availability"],
                }))
              }
              value={filters.availability}
            >
              <option value="all">Tous les documents</option>
              <option value="full_text">Full article</option>
              <option value="abstract_only">Abstract only</option>
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
