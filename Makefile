extension: clean src/viperoffice.py src/editor.py src/sentence_motions.py src/core.py
	@if [ -z "$$VIPEROFFICE_VERSION" ]; then \
		echo "VIPEROFFICE_VERSION must be set"; \
		exit 1; \
	fi
	@mkdir -p build dist
	@cp -r extension/template build/template
	@cp src/viperoffice.py build/template/Scripts/python/viperoffice.py
	@cp src/editor.py build/template/Scripts/python/editor.py
	@cp src/sentence_motions.py build/template/Scripts/python/sentence_motions.py
	@cp src/core.py build/template/Scripts/python/core.py
	@sed -i "s/%VIPEROFFICE_VERSION%/$$VIPEROFFICE_VERSION/g" build/template/description.xml
	@cd build/template && zip -r "../../dist/viperoffice-python.oxt" .

.PHONY: clean extension
clean:
	rm -rf build dist
