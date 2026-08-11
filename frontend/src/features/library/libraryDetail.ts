export const librarySplitViewMediaQuery = "(min-width: 80rem)";

export function shouldOpenLibraryDetailDialog(
  hasExplicitSelection: boolean,
  splitViewVisible: boolean,
): boolean {
  return hasExplicitSelection && !splitViewVisible;
}
