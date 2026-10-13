import info from "./legal-info.json";

function fill(template: string): string {
  return template.replaceAll("{brand}", info.brand).replaceAll("{legal_name}", info.legal_name);
}

/**
 * The one legal-operator record. Edit `lib/legal-info.json`: `legal_name` is
 * the operating entity, `governing_law` is the state, `contact` is the mailbox.
 * The two public sentences are filled from the templates in that file.
 */
export const legal = info;

/** Legal-page lead and metadata, filled from `operator_template`. */
export const operatorLine = fill(info.operator_template);

/** Terms-of-service party, filled from `terms_party_template`. */
export const termsParty = fill(info.terms_party_template);
