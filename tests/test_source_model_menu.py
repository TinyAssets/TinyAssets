"""Source grouping in the actual page controller, with no external network."""
from pathlib import Path

import pytest


def test_models_start_collapsed_under_named_sources_with_automatic_selected():
    from playwright.sync_api import expect, sync_playwright

    source = Path("tinyassets/onboarding/app/main.js").read_text("utf-8")
    start = source.index("  const ModelPicker={")
    controller = source[start:source.index("\n  };", start) + 6]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content('<div id="model-menu"></div>')
        page.add_script_tag(content='const $=id=>document.getElementById(id);' + controller + """
          ModelPicker.snapshot={preferences:{policy:{mode:'automatic'}},sources:[
            {provider_ref:'internal-one',display_name:'First source'},
            {provider_ref:'internal-two',display_name:'api.example.org'}],
            options:['internal-one','internal-two'].flatMap(provider_ref=>['one','two'].map(
              model_id=>({reference:{provider_ref,model_id},in_candidate_catalog:true,
                freshness:'fresh',reasons:[]}))),unavailable:[{reference:{provider_ref:'internal-one',
              model_id:'needs-access'},reasons:[{reason:'source_not_accepted'}]}]};
          ModelPicker.stale=false;ModelPicker.expires=Date.now()+60000;
          ModelPicker.renderMenu();
        """)
        automatic = page.get_by_role('menuitemradio', name='Automatic', exact=True)
        expect(automatic).to_have_attribute('aria-checked', 'true')
        assert page.locator('#model-menu > button').first.inner_text().endswith('Automatic')
        group = page.get_by_role('menuitemradio', name='First source', exact=False)
        expect(group).to_have_attribute('aria-expanded', 'false')
        assert page.locator('.model-menu-item--nested').count() == 0
        group.click()
        expect(group).to_have_attribute('aria-expanded', 'true')
        assert page.locator('.model-menu-item--nested').all_inner_texts() == [
            'one', 'two', 'needs-accesssource not accepted',
        ]
        assert 'internal-' not in page.locator('#model-menu').inner_text()
        group.click()
        assert page.locator('.model-menu-item--nested').count() == 0
        browser.close()


pytestmark = pytest.mark.verifies('agent-model-selection:Connection-scoped model choices')
