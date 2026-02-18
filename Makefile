extension: clean src/vibreoffice.py
	@if [ -z "$$VIBREOFFICE_VERSION" ]; then \
		echo "VIBREOFFICE_VERSION must be set"; \
		exit 1; \
	fi
	@mkdir -p build dist
	@cp -r extension/template build/template
	@cp src/vibreoffice.py build/template/Scripts/python/vibreoffice.py
	@sed -i "s/%VIBREOFFICE_VERSION%/$$VIBREOFFICE_VERSION/g" build/template/description.xml
	@cd build/template && zip -r "../../dist/vibreoffice-python.oxt" .

.PHONY: clean extension
clean:
	rm -rf build dist
