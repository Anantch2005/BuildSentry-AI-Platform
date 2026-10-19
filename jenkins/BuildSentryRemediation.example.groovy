@Library('BuildSentry') _

pipeline {
    agent any

    options {
        skipDefaultCheckout(true)
        timestamps()
    }

    parameters {
        string(
            name: 'AUTOHEAL_ACTION',
            defaultValue: '',
            description: 'Internal BuildSentry action; leave empty for a normal build.'
        )
        booleanParam(
            name: 'PUSH_IMAGE',
            defaultValue: false,
            description: 'Push the image only after configuring registry credentials.'
        )
    }

    environment {
        IMAGE_NAME = 'example/buildsentry-demo'
    }

    stages {
        stage('Checkout') {
            steps {
                script { buildsentry(stage: 'checkout') }
                checkout scm
            }
        }

        stage('Test') {
            steps {
                script { buildsentry(stage: 'test') }
                sh '''
                    set -eu
                    if [ -f requirements.txt ]; then
                        if [ -x .venv/bin/python ]; then
                            .venv/bin/python -m pytest
                        else
                            python -m pytest
                        fi
                    elif [ -f package.json ]; then
                        npm test
                    else
                        echo 'Add your project-specific test command here.'
                    fi
                '''
            }
        }

        stage('Docker Build') {
            when { expression { fileExists('Dockerfile') } }
            steps {
                script { buildsentry(stage: 'docker') }
                sh '''
                    set -eu
                    CACHE_FLAG=''
                    if [ "${BUILDSENTRY_DOCKER_NO_CACHE:-false}" = 'true' ]; then
                        CACHE_FLAG='--no-cache'
                    fi
                    docker build $CACHE_FLAG -t "$IMAGE_NAME:$BUILD_NUMBER" .
                '''
            }
        }

        stage('Docker Push') {
            when { expression { fileExists('Dockerfile') && params.PUSH_IMAGE } }
            steps {
                script { buildsentry(stage: 'registry') }
                echo 'Authenticate to your registry using Jenkins credentials before pushing.'
                sh 'docker push "$IMAGE_NAME:$BUILD_NUMBER"'
            }
        }
    }

    post {
        failure {
            script { buildsentry() }
        }
    }
}